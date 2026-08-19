# Kitty graphics protocol support

This page explains how wezterm implements the [kitty graphics protocol][kitty-protocol]:
how an image escape sequence sent by a program is parsed, stored, sliced onto the cell grid,
rendered, and deleted.

It is written for wezterm contributors: it assumes familiarity with the protocol's escape grammar
and focuses on where in the codebase each piece lives and how the pieces fit together.
Each section starts with a one-line recap of the relevant protocol concept.

Support is gated by the `enable_kitty_graphics` configuration option; when it is disabled, kitty
escape sequences are ignored (`TerminalState::kitty_img` returns early).

[kitty-protocol]: https://sw.kovidgoyal.net/kitty/graphics-protocol/

## Terminology

The protocol distinguishes the image *data* from its *placement* on the screen, and both can be
named.
The codebase keeps those names on every `ImageCell` as `image_id` and `placement_id`.

| Protocol | Escape key | wezterm code |
|---|---|---|
| image id | `i=` | `image_id` — identity of the image data |
| image number | `I=` | also mapped to an `image_id` (auto-allocated when the program only supplies `I=`) |
| placement id | `p=` | `placement_id` — identity of one placement of an image |
| delete verb | `d=` | `KittyImageDelete` variants |

So the protocol's two image references (`i=` and `I=`) collapse into the single `image_id` field
on `ImageCell`/`ImageAttachParams`.
The `(image_id, placement_id)` pair is what the rest of this page refers to as "the placement".

## How images are handled: input code flow

Recap: a program first *transmits* pixel data (`a=t`/`a=p`), optionally *displays* it by
*placing* it onto the grid (`a=d`/`a=p`, with geometry in `x/y/w/h/c/r` and an optional name in
`p=`), can add or edit animation frames (`a=f`/`a=c`), query status, and *delete* placements
(`a=d`, `d=i/I/n/...`).

The path from escape sequence to screen:

1. **Parse**: The escape parser (`wezterm-escape-parser`, `apc.rs`) turns the `\x1b_G...\x1b\\`
   payload into a `KittyImage` enum value: `TransmitData`, `TransmitDataAndDisplay`, `Display`,
   `Delete`, `Query`, `TransmitFrame` or `ComposeFrame`, with structured `KittyImageTransmit`,
   `KittyImagePlacement` and `KittyImageDelete` data.
   The performer feeds it to `TerminalState::kitty_img`.
2. **Dispatch**: `kitty_img` (`term/src/terminalstate/kitty.rs`) checks the feature gate,
   coalesces multi-chunk transmissions (data that arrived over several escapes is reassembled in
   `KittyImageState::accumulator`), and routes to the relevant handler.
3. **Transmit**: `kitty_img_transmit` decodes the payload (RGBA/RGB/PNG, with optional deflate),
   assigns an `image_id` — the explicit `i=`, or a fresh id derived from `max_image_id` when only
   `I=` was given — records the image in `id_to_data` (and `number_to_id`), and answers any query.
4. **Place**: `kitty_img_place` resolves the `image_id` (via `number_to_id` if needed), removes any
   previous placement with the same `(image_id, placement_id)` pair, then slices it into the grid.
5. **Slice**: `assign_image_to_cells` (`term/src/terminalstate/image.rs`) computes how many cells
   the placement spans (explicit `c=`/`r=`, or derived from the pixel size divided by the cell
   size) and writes one `ImageCell` per covered cell.
   All cells of a placement share the same `Arc<ImageData>` and the same `image_id`/`placement_id`;
   each cell stores its own normalized texture rect and edge padding.
   Kitty placements *attach* to the cell (`attach_image`, stacking by z-index);
   sixel and iTerm2 images *replace* the cell's images (`set_image`).
6. **Cursor**: Unless `do_not_move_cursor` (`C=1`) was given, the cursor is moved past the
   bottom-right corner of the placement.

As a compact summary of the same flow:
```
program
  │  writes \x1b_G ... \x1b\\    (kitty graphics escape)
  ▼
escape parser               wezterm-escape-parser / apc.rs
  │  emits a KittyImage (transmit / display / delete / frame / query)
  ▼
TerminalState::kitty_img    term/src/terminalstate/kitty.rs
  │  feature gate + reassemble multi-chunk payloads
  ├─► kitty_img_transmit ──► ImageDataType ──► id_to_data[image_id]
  ├─► kitty_img_place ──► assign_image_to_cells
  │                        └─► one ImageCell per covered cell
  └─► delete / frame handlers

cell grid ◄── each CellAttributes holds its ImageCells, sorted by z_index
```

## How the image is stored

Recap: the protocol names pixel data with `i=`/`I=` and keeps it in the terminal independent of
any placement, so the same data can be placed several times.

The payload lives in `ImageDataType` (`wezterm-cell/src/image.rs`), an enum whose variants cover
the storage forms: `EncodedFile` (encoded bytes held in memory), `EncodedLease` (encoded bytes
moved to an on-disk blob via the blob manager), `Rgba8` (decoded single frame) and `AnimRgba8`
(decoded animation: frames plus durations).
The decoded variants cache a sha256 of their frame data.

`ImageData` is the shareable wrapper around a payload: an `ImageDataType` behind a `Mutex`, plus a
content-identity hash captured at construction time. `PartialEq` compares hashes only.
That hash is the dedup key — the same pixels are stored once and referenced by many cells — and the
wire identity (see the remote/mux section).

On transmit, `raw_image_to_image_data` hashes the decoded payload and consults the `image_cache`
(a small LRU), so repeatedly transmitted identical images reuse the same `Arc<ImageData>`;
otherwise the data is `swap_out()`-ed (optionally to a blob lease) and wrapped.

`KittyImageState` keeps the live images in `id_to_data` (keyed by `image_id`), with
`number_to_id` mapping image numbers to ids.
Reusing an image id replaces the previous data, per the protocol.
Retained data is bounded by `used_memory` (a fixed 320 MiB budget, not yet configurable);
`prune_unreferenced` evicts data that no placement references.

Animation frames (`TransmitFrame`, `ComposeFrame`) mutate the decoded frames in place and refresh
the per-frame hashes; see the quirks section for the one caveat about the identity hash.

## How cells decide to render an image

Recap: a placement is a rectangular region of cells that each display a slice of one image.

Cells are text-or-image slots, and a cell can carry several images at once.
`CellAttributes` keeps them in an image list sorted by z-index:
- `attach_image` binary-inserts,
- `set_image` replaces the whole list,
- `clear_images` empties it.

Each entry is an `ImageCell` describing one cell-sized slice:

- `top_left`/`bottom_right` — normalized texture coordinates of the slice within the image;
- `data` — the shared `Arc<ImageData>`;
- `z_index` — negative renders beneath the text layer, non-negative overlays it;
- per-edge pixel `padding_*` — sub-cell offsets used by the kitty protocol;
- `image_id`/`placement_id` — the placement identity described above.

During rendering, the screen-line renderer (`wezterm-gui`, `render/screen_line.rs`) walks the
line clusters and, for each one, draws images with negative z-index first, then the text, then
images with non-negative z-index on top.

Change detection is driven by shape hashes: each `ImageCell` contributes to its line's shape hash
via `compute_shape_hash`, which covers the layout (texture rect, padding, z-index, ids) and the
image *identity* (its hash), but not the pixels.
An in-place pixel edit therefore does not invalidate the line's shape hash.

## Placement lifecycle

Recap: placements can be named (`p=`), re-placed (which replaces the old one), and deleted via
the `d=` verb, which can target a single placement, an image, or everything.

`KittyImageState::placements` is a registry keyed by `(image_id, placement_id)`, mapping to
`PlacementInfo { first_row, rows, cols }` — the screen region the placement occupies.
This is what makes a placement addressable.

- **Place**: `kitty_img_place` inserts the entry after slicing.
  Re-placing the same `(image_id, placement_id)` pair first removes the old placement, so the
  cells are sliced again.
- **Delete**: The `d=` verb routes to `kitty_remove_placement(image_id, placement_id)`:
  A `Some(p)` placement id removes exactly that pair, while `None` removes every placement of that
  image.
  For each removed placement, `kitty_remove_placement_from_model` recomputes the cell range from
  `PlacementInfo` and calls `detach_image_with_placement` on each cell in it, which removes the
  matching `ImageCell`s from their `CellAttributes`.
  Uppercase delete verbs (e.g. `d=I`) also release the image data; `d=a` and terminal reset clear
  all placements.
- **Carry-forward**: A placement that names itself with `p=` is persistent: when one of its cells
  is overwritten by new text, `VecStorage::set_cell` copies the images carrying a placement id
  onto the replacement cell, so the image keeps its identity and stays deletable.
  Unnamed placements are transient — writing over them removes the image.
  (The opt-out path, `Line::set_cell_clearing_image_placements`, exists but is currently unused.)

Note that the registry only tracks placements, not raw image data: unreferenced data stays in
`id_to_data` until it is explicitly deleted or evicted by the memory budget.

## Worked example: transmitting and placing an image

Concretely: a program transmits a 400×200 RGBA image, then displays it as a 4-by-2 block of
cells, naming the image `7` and the placement `5`.

```
# 1. transmit the pixels, naming the image 7
kitty_transmit(image_id=7, format=rgba, width=400, height=200, data=<400*200*4 bytes>)

# 2. display it over a 4x2-cell block, naming the placement 5
kitty_display(image_id=7, placement_id=5, columns=4, rows=2, z_index=1)
```

After the transmit, the image is stored once and addressable by its id:
```
id_to_data[7] = ImageData(400x200, Rgba8, hash=H)
number_to_id  = {}
```

After the display, the placement is registered and each covered cell holds a slice:
```
placements[(7, Some(5))] = PlacementInfo { first_row: R, rows: 2, cols: 4 }

each of the 8 covered cells (rows R..R+2, cols C..C+4) carries an ImageCell:
  ImageCell {
    image_id:     Some(7),
    placement_id: Some(5),
    z_index:      1,
    data:         Arc::clone(id_to_data[7]),
    texture rect: that cell's slice of the normalized image (0,0)..(1,1),
    padding:      remainder pixels on the cells along the right/bottom edge,
  }
```

What happens next follows from the placement rules above:

- Typing over one of those cells keeps the placement alive, because its `ImageCell`s carry a
  placement id (carry-forward).
- `d=i,i=7,p=5` detaches all 8 cells via `detach_image_with_placement`.
- `d=I,i=7` additionally frees the image data from `id_to_data`.
- Displaying the same pair again replaces the placement: the old cells are detached first, then
  the new ones are sliced.

## Remote / mux code flow

When the terminal runs locally, the cells above are rendered directly.
With a mux (or any remote domain), images travel separately from the cell grid:

- `SerializedLines` strips each cell's images into `SerializedImageCell` records
  (`codec/src/lib.rs`) — texture rect, paddings, z-index, both ids, and `data_hash` (the image's
  identity hash) — then clears the images off the cells.
  The rest of the line serializes without them.
- The pixel payload is fetched on demand: when a client encounters a `data_hash` it does not have,
  it issues a `GetImageCell` RPC.
  The mux server (`wezterm-mux-server-impl`, `sessionhandler.rs`) finds the cell on the pane,
  matches `data_hash`, and returns the `Arc<ImageData>`.
  Clients keep an LRU of received images so repeated hashes hit the cache.
- The client rebuilds each `ImageCell` with `ImageCell::with_z_index`, restoring `image_id` and
  `placement_id`, so the placement identity (and its carry-forward/deletion semantics) survives
  the round trip.

## Known quirks and limitations

- **Stale identity hash after in-place edits**: `TransmitFrame`/`ComposeFrame` mutate the decoded
  frames in place and refresh the *inner* per-frame hashes, but not the `ImageData` identity hash
  captured at construction.
  After a compose, the identity hash can disagree with the pixel content.
  This is benign for dedup and rendering (pixels are keyed by their own content hashes) but means
  identity is not a live checksum.
- **No scrollback trim**: When lines are popped off the top of the scrollback
  (`Screen::lines.pop_front`), the `placements` registry and `id_to_data` are not reconciled.
  Scrolled-out cells keep their `Arc<ImageData>` — which is what makes the image still render
  when scrolling back — but named placements on truncated lines stay registered forever, and
  their data is treated as permanently referenced (immune to the memory budget).
- **Unnamed placement collision**: The registry key is `(image_id, Option<u32>)`, so two
  placements of the same image that both omit `p=` collide: only the last is registered, even
  though the cells are sliced for both.
  Deletion then targets the surviving entry.
