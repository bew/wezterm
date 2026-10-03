use super::glyphcache::GlyphCache;
use ::window::bitmaps::atlas::{OutOfTextureSpace, Sprite};
use ::window::bitmaps::{BitmapImage, Image};
use ::window::color::SrgbaPixel;
use ::window::{Point, Rect, Size};
use anyhow::Context;
use config::DimensionContext;
use std::rc::Rc;
use wezterm_font::units::*;
use wezterm_font::{FontConfiguration, FontMetrics};

/// Pixel geometry for a terminal cell, derived from font metrics + config.
///
/// Used to convert between the cell grid and pixels, and to position text and
/// its decorations.
#[derive(Copy, Clone, Debug)]
pub struct RenderMetrics {
    /// Negative offset _added_ to the cell's bottom edge to compute the text baseline.
    /// note: `baseline = cell_height + descender`.
    ///
    /// Stored as a fractional pixel distance so subpixel precision can survives subsequent
    /// scaling applied by [`RenderMetrics::scale_line_height`].
    pub descender: PixelLength,
    /// Row within a cell at which an underline is drawn, counted from top edge.
    pub descender_row: IntPixelLength,
    /// Row at which the second stroke of a double underline is drawn, counted from top edge.
    pub descender_plus_two: IntPixelLength,
    /// Thickness of underline and strikethrough strokes, in physical pixels.
    /// At least 1.
    pub underline_height: IntPixelLength,
    /// Row at which a strikethrough is drawn, counted from top edge.
    pub strike_row: IntPixelLength,
    /// Width and height of a single terminal cell, in physical pixels.
    /// Rounded up to whole pixels.
    pub cell_size: Size,
}

impl RenderMetrics {
    /// Builds metrics from raw font metrics.
    ///
    /// Unlike [`RenderMetrics::new`], no overrides like line-height / cell-width scaling
    /// from config are applied.
    pub fn with_font_metrics(metrics: &FontMetrics) -> Self {
        let (cell_height, cell_width) = (
            metrics.cell_height.get().ceil() as usize,
            metrics.cell_width.get().ceil() as usize,
        );

        let underline_height = metrics.underline_thickness.get().round().max(1.) as isize;

        let descender_row =
            (cell_height as f64 + (metrics.descender - metrics.underline_position).get()) as isize;
        let descender_plus_two =
            (2 * underline_height + descender_row).min(cell_height as isize - underline_height);
        let strike_row = descender_row / 2;

        Self {
            descender: metrics.descender,
            descender_row,
            descender_plus_two,
            strike_row,
            cell_size: Size::new(cell_width as isize, cell_height as isize),
            underline_height,
        }
    }

    /// Returns a copy scaled by a line-height factor.
    ///
    /// The cell grows or shrinks and the decoration rows shift to keep the text
    /// vertically centered.
    pub fn scale_line_height(&self, line_height: f64) -> Self {
        let size = euclid::size2(
            self.cell_size.width,
            (self.cell_size.height as f64 * line_height) as isize,
        );

        let adjust = (((self.descender_row as f64 * line_height) - self.descender_row as f64) / 2.0)
            as isize;
        Self {
            descender: self.descender - PixelLength::new(adjust as f64),
            descender_row: self.descender_row - adjust,
            descender_plus_two: self.descender_plus_two - adjust,
            underline_height: self.underline_height,
            strike_row: self.strike_row,
            cell_size: size,
        }
    }

    /// Returns a copy with the cell width multiplied by `scale`.
    pub fn scale_cell_width(&self, scale: f64) -> Self {
        let mut scaled = *self; // make a copy
        scaled.cell_size.width = (self.cell_size.width as f64 * scale) as isize;
        scaled
    }

    /// Builds the window's font metrics from the configuration's default font.
    ///
    /// Applies the config `line_height` and `cell_width` scaling, plus any
    /// config overrides for underline thickness, underline position and
    /// strikethrough position.
    /// Can fail if the font metrics are unavailable.
    pub fn new(fonts: &Rc<FontConfiguration>) -> anyhow::Result<Self> {
        let metrics = fonts
            .default_font_metrics()
            .context("failed to get font metrics!?")?;

        let line_height = fonts.config().line_height;
        let cell_width = fonts.config().cell_width;

        let (cell_height, cell_width) = (
            (metrics.cell_height.get() * line_height).ceil() as usize,
            (metrics.cell_width.get() * cell_width).ceil() as usize,
        );

        // When line_height != 1.0, we adjust the text baseline position so the text stays
        // vertically centered.
        let line_height_y_adjust = (cell_height as f64 - metrics.cell_height.get().ceil()) / 2.;

        let config = fonts.config();
        let underline_height = match &config.underline_thickness {
            None => metrics.underline_thickness.get().round().max(1.) as isize,
            Some(d) => d
                .evaluate_as_pixels(DimensionContext {
                    dpi: fonts.get_dpi() as f32,
                    pixel_max: metrics.underline_thickness.get() as f32,
                    pixel_cell: cell_height as f32,
                })
                .max(1.) as isize,
        };

        let underline_position = match &config.underline_position {
            None => metrics.underline_position.get(),
            Some(d) => d.evaluate_as_pixels(DimensionContext {
                dpi: fonts.get_dpi() as f32,
                pixel_max: metrics.underline_position.get() as f32,
                pixel_cell: cell_height as f32,
            }) as f64,
        };

        let descender_row = (cell_height as f64 + (metrics.descender.get() - underline_position)
            - line_height_y_adjust) as isize;
        let descender_plus_two =
            (2 * underline_height + descender_row).min(cell_height as isize - underline_height);
        let strike_row = match &config.strikethrough_position {
            None => {
                ((cell_height as f64 + (metrics.descender.get() - underline_position)) / 2.)
                    as isize
            }
            Some(d) => d
                .evaluate_as_pixels(DimensionContext {
                    dpi: fonts.get_dpi() as f32,
                    pixel_max: descender_row as f32 / 2.,
                    pixel_cell: cell_height as f32,
                })
                .round() as isize,
        };

        Ok(Self {
            descender: metrics.descender - PixelLength::new(line_height_y_adjust),
            descender_row,
            descender_plus_two,
            strike_row,
            cell_size: Size::new(cell_width as isize, cell_height as isize),
            underline_height,
        })
    }
}

pub struct UtilSprites {
    pub white_space: Sprite,
    pub filled_box: Sprite,
}

impl UtilSprites {
    pub fn new(
        glyph_cache: &mut GlyphCache,
        metrics: &RenderMetrics,
    ) -> Result<Self, OutOfTextureSpace> {
        let mut buffer = Image::new(
            metrics.cell_size.width as usize,
            metrics.cell_size.height as usize,
        );

        let black = SrgbaPixel::rgba(0, 0, 0, 0);
        let white = SrgbaPixel::rgba(0xff, 0xff, 0xff, 0xff);

        let cell_rect = Rect::new(Point::new(0, 0), metrics.cell_size);

        buffer.clear_rect(cell_rect, white);
        let filled_box = glyph_cache.atlas.allocate(&buffer)?;

        buffer.clear_rect(cell_rect, black);
        let white_space = glyph_cache.atlas.allocate(&buffer)?;

        Ok(Self {
            white_space,
            filled_box,
        })
    }
}
