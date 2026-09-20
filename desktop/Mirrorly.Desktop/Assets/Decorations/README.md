# Spring botanical decorations

Original SVG artwork authored for Mirrorly Visual Baseline v1. No downloaded art,
embedded raster image, font, external reference, script, filter or animation.
WinUI's standard `SvgImageSource` renders paths, gradients and translucent layers.

- `spring-sprig-sidebar.svg`: diagonal upward growth; tip-side pink blossom is
  above/left of the main stem, the second is below/right. Preserve this staggered
  composition; do not move both flowers to one side or below the branch.
- `spring-sprig-header.svg`: a separate, smaller composition with the same palette
  and line language. All flowers/leaves attach to stems; no floating petals.

Each SVG owns its illustration palette in `defs`. These shading colors belong to
the artwork, not to functional status semantics. App colors remain centralized in
`Themes/MirrorlyResources.xaml`. Replace one or both SVGs to replace the artwork;
no page needs to change. Keep the same filenames, transparent background and
viewBox proportions unless the placement resource is intentionally adjusted.

`Components/SpringSprig` is the shared noninteractive presenter. Header/sidebar
styles in the central resources own fixed layout slots; the image is drawn inside
a Canvas so intrinsic SVG bounds cannot change page measurement. Position and
scale use render transforms; High Contrast collapses the presenter. Existing
Home state/narrow-window decoration gates still apply.

Future petals are specified in [MOTION](../../../../docs/gui/MOTION.md), not in
these static assets. See the [WinUI SvgImageSource reference](https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/microsoft.ui.xaml.media.imaging.svgimagesource)
for the standard rendering mechanism.
