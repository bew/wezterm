pub use wezterm_input_types::PixelUnit;

/// A signed distance in a window's client-area pixel space, carried in `f64`.
/// Fractional values survive scaling, addition and subtraction.
/// Round it up to a whole pixel when an integer coordinate is needed.
pub type PixelLength = euclid::Length<f64, PixelUnit>;

/// A signed whole-pixel distance in a window's client-area pixel space.
/// Backed by a bare `isize`, so it is interchangeable with any other `isize`.
/// Use it where a value must land on an exact pixel, such as a cell-row offset
/// or a decoration stroke thickness.
pub type IntPixelLength = isize;
