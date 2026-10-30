# GamedevX: Thai font support
# Replaces Ren'Py's default font (DejaVuSans, which has no Thai glyphs) with a Thai font.
# The font is Loma by TLWG (GPL-2+ with font exception), see fonts/LICENSE-Loma.txt.
# Want another Thai font (e.g. Sarabun, Kanit)? Put the file in game/fonts/ and change the paths below.

init -1 python:
    for _gdx_f in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        config.font_replacement_map[_gdx_f, False, False] = ("fonts/thai.otf", False, False)
        config.font_replacement_map[_gdx_f, True, False] = ("fonts/thai-bold.otf", False, False)
        config.font_replacement_map[_gdx_f, False, True] = ("fonts/thai.otf", False, False)
        config.font_replacement_map[_gdx_f, True, True] = ("fonts/thai-bold.otf", False, False)
