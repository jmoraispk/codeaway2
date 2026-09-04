from codeaway.desktop import FractionalRegion, PixelPoint, PixelRegion


def test_fractional_region_resolves_inside_window():
    window = PixelRegion(100, 50, 1000, 800)
    surface = FractionalRegion(0.2, 0.1, 0.5, 0.75)

    assert surface.resolve(window) == PixelRegion(300, 130, 500, 600)
    assert surface.resolve(window).center == PixelPoint(550, 430)
