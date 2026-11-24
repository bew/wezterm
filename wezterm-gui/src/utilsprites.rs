use super::glyphcache::GlyphCache;
use ::window::bitmaps::atlas::{OutOfTextureSpace, Sprite};
use ::window::bitmaps::{BitmapImage, Image};
use ::window::color::SrgbaPixel;
use ::window::{Point, Rect, Size};
use anyhow::Context;
use config::{Dimension, DimensionContext};
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
    /// note: `text_baseline = cell_height + font_descender_offset`.
    ///
    /// Stored as a fractional pixel distance so subpixel precision can survive subsequent
    /// scaling applied by [`RenderMetrics::scale_line_height`].
    pub font_descender_offset: PixelLength,
    /// Thickness of underline and strikethrough strokes, in physical pixels.
    /// At least 1.
    pub font_line_thickness: IntPixelLength,
    /// Row within a cell at which an underline is drawn, counted from top edge.
    pub underline_row: IntPixelLength,
    /// Row at which the second stroke of a double underline is drawn, counted from top edge.
    pub underline_2nd_row: IntPixelLength,
    /// Row at which a strikethrough is drawn, counted from top edge.
    pub strikethrough_row: IntPixelLength,
    /// Width and height size of a single terminal cell, in physical pixels.
    /// Rounded up to whole pixels.
    pub cell_size: Size,
}

/// Config-driven decoration overrides.
///
/// All `None` on the raw-font path ([`RenderMetrics::with_font_metrics`]); the terminal
/// path ([`RenderMetrics::new`]) fills them from the config.
#[derive(Clone, Copy, Default)]
struct RenderDecorationOverrides<'a> {
    /// Overrides the font's underline / strikethrough stroke thickness.
    line_thickness: Option<&'a Dimension>,
    /// Overrides the font's underline position.
    underline_position: Option<&'a Dimension>,
    /// Overrides the default strikethrough position.
    strikethrough_position: Option<&'a Dimension>,
}

impl RenderMetrics {
    /// Single source of truth for the cell + decoration geometry.
    ///
    /// Both public constructors funnel through here, so each row computation logic is written once.
    fn build(
        metrics: &FontMetrics,
        line_height_factor: f32,
        cell_width_factor: f32,
        overrides: RenderDecorationOverrides<'_>,
        dpi: f32,
    ) -> Self {
        let raw_cell_height = metrics.cell_height.get() as f32;
        let cell_height = (raw_cell_height * line_height_factor).ceil();
        let cell_width = (metrics.cell_width.get() as f32 * cell_width_factor).ceil();

        // When line_height_factor != 1.0, shift the baseline down by half the growth so the text
        // stays vertically centered.
        let line_height_y_adjust = (cell_height - raw_cell_height.ceil()) / 2.;

        let dim_ctx = |pixel_max: f32| DimensionContext {
            dpi,
            pixel_max,
            pixel_cell: cell_height,
        };

        let base_font_line_thickness = metrics.underline_thickness.get() as f32;
        let font_line_thickness = match overrides.line_thickness {
            None => base_font_line_thickness.round(),
            Some(d) => d.evaluate_as_pixels(dim_ctx(base_font_line_thickness)),
        };
        let font_line_thickness = font_line_thickness.max(1.) as isize; // Clamp to at least 1px

        let base_underline_position = metrics.underline_position.get() as f32;
        let underline_position = match overrides.underline_position {
            None => base_underline_position,
            Some(d) => d.evaluate_as_pixels(dim_ctx(base_underline_position)),
        };

        let base_font_descender = metrics.descender_offset.get() as f32;
        let underline_row = (cell_height + (base_font_descender - underline_position)
            - line_height_y_adjust) as isize;
        let underline_2nd_row = (2 * font_line_thickness + underline_row)
            .min(cell_height as isize - font_line_thickness);
        let strikethrough_row = match overrides.strikethrough_position {
            None => ((cell_height + (base_font_descender - underline_position)) / 2.) as isize,
            Some(d) => d
                .evaluate_as_pixels(DimensionContext {
                    dpi,
                    pixel_max: underline_row as f32 / 2.,
                    pixel_cell: cell_height,
                })
                .round() as isize,
        };

        Self {
            font_descender_offset: metrics.descender_offset
                - PixelLength::new(line_height_y_adjust as f64),
            underline_row,
            underline_2nd_row,
            strikethrough_row,
            cell_size: Size::new(cell_width as isize, cell_height as isize),
            font_line_thickness,
        }
    }

    /// Builds metrics from raw font metrics.
    ///
    /// Unlike [`RenderMetrics::new`], no overrides like line-height / cell-width scaling
    /// from config are applied.
    pub fn with_font_metrics(metrics: &FontMetrics) -> Self {
        Self::build(metrics, 1.0, 1.0, RenderDecorationOverrides::default(), 0.0)
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

        let config = fonts.config();

        Ok(Self::build(
            &metrics,
            config.line_height as f32,
            config.cell_width as f32,
            RenderDecorationOverrides {
                line_thickness: config.underline_thickness.as_ref(),
                underline_position: config.underline_position.as_ref(),
                strikethrough_position: config.strikethrough_position.as_ref(),
            },
            fonts.get_dpi() as f32,
        ))
    }

    /// Returns a copy scaled by a line-height factor.
    ///
    /// The cell grows or shrinks and the baseline and decoration rows shift down by
    /// half the growth, using the same centering rule as [`RenderMetrics::build`].
    ///
    /// Unlike [`RenderMetrics::build`], this nudges the existing geometry instead of
    /// recomputing it from the raw font metrics, so the two paths can differ by ~1px.
    /// The unit tests assert that tolerance; treat the result as approximate, not exact.
    pub fn scale_line_height(&self, line_height_factor: f64) -> Self {
        let old_height = self.cell_size.height;
        let new_height = (old_height as f64 * line_height_factor) as isize;

        let y_adjust = (new_height - old_height) / 2;

        Self {
            font_descender_offset: self.font_descender_offset - PixelLength::new(y_adjust as f64),
            underline_row: self.underline_row + y_adjust,
            underline_2nd_row: (2 * self.font_line_thickness + self.underline_row + y_adjust)
                .min(new_height - self.font_line_thickness),
            strikethrough_row: self.strikethrough_row + y_adjust,
            font_line_thickness: self.font_line_thickness,
            cell_size: Size::new(self.cell_size.width, new_height),
        }
    }

    /// Returns a copy with the cell width multiplied by `factor`.
    pub fn scale_cell_width(&self, factor: f64) -> Self {
        let mut scaled = *self; // make a copy
        scaled.cell_size.width = (self.cell_size.width as f64 * factor) as isize;
        scaled
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

#[cfg(test)]
mod tests {
    use super::*;
    use termwiz::cell::Presentation;

    /// Builds a `FontMetrics` with the fields under test set; the rest are dummies.
    fn font_metrics(
        cell_height: f64,
        cell_width: f64,
        descender_offset: f64,
        underline_position: f64,
        underline_thickness: f64,
    ) -> FontMetrics {
        FontMetrics {
            cell_width: PixelLength::new(cell_width),
            cell_height: PixelLength::new(cell_height),
            descender_offset: PixelLength::new(descender_offset),
            underline_thickness: PixelLength::new(underline_thickness),
            underline_position: PixelLength::new(underline_position),
            cap_height_ratio: None,
            cap_height: None,
            is_scaled: true,
            presentation: Presentation::Text,
            force_y_adjust: PixelLength::new(0.),
        }
    }

    /// A 20x10 cell, descender_offset -4, underline 3px below the baseline.
    fn sample_font_metrics() -> FontMetrics {
        font_metrics(20., 10., -4., 3., 1.)
    }

    fn at_line_height(metrics: &FontMetrics, line_height_factor: f64) -> RenderMetrics {
        RenderMetrics::build(
            metrics,
            line_height_factor as f32,
            1.,
            RenderDecorationOverrides::default(),
            96.,
        )
    }

    #[test]
    fn unit_line_height_uses_font_metrics() {
        let rm = at_line_height(&sample_font_metrics(), 1.0);

        assert_eq!(rm.cell_size, Size::new(10, 20));
        assert_eq!(rm.font_descender_offset.get(), -4.);
        assert_eq!(rm.font_line_thickness, 1);
        assert_eq!(rm.underline_row, 13);
        assert_eq!(rm.underline_2nd_row, 15);
        assert_eq!(rm.strikethrough_row, 6);
    }

    #[test]
    fn underline_tracks_baseline_across_line_heights() {
        let metrics = sample_font_metrics();
        let underline_position = metrics.underline_position.get();

        for line_height_factor in [1.0, 1.25, 1.5, 1.75, 2.0] {
            let rm = at_line_height(&metrics, line_height_factor);
            let baseline = rm.cell_size.height as f64 + rm.font_descender_offset.get();

            // Independent truncation of each row can differ by up to 1 pixel.
            assert!(
                (rm.underline_row as f64 - (baseline - underline_position)).abs() <= 1.0,
                "underline_row ({}) should track the baseline at line_height={line_height_factor}",
                rm.underline_row
            );
        }
    }

    #[test]
    fn scale_line_height_keeps_underline_on_baseline() {
        let metrics = sample_font_metrics();
        let underline_position = metrics.underline_position.get();

        for line_height in [1.0, 1.25, 1.5, 1.75, 2.0] {
            let rm = RenderMetrics::with_font_metrics(&metrics).scale_line_height(line_height);
            let baseline = rm.cell_size.height as f64 + rm.font_descender_offset.get();

            assert!(
                (rm.underline_row as f64 - (baseline - underline_position)).abs() <= 1.0,
                "underline_row ({}) should track the baseline at line_height={line_height}",
                rm.underline_row
            );
        }
    }

    #[test]
    fn config_overrides_win_over_font_metrics() {
        let overrides = RenderDecorationOverrides {
            line_thickness: Some(&Dimension::Pixels(4.)),
            underline_position: Some(&Dimension::Pixels(2.)),
            strikethrough_position: Some(&Dimension::Pixels(5.)),
        };
        let rm = RenderMetrics::build(&sample_font_metrics(), 1., 1., overrides, 96.);

        assert_eq!(rm.font_line_thickness, 4);
        assert_eq!(rm.underline_row, 14);
        assert_eq!(rm.underline_2nd_row, 16);
        assert_eq!(rm.strikethrough_row, 5);
    }
}
