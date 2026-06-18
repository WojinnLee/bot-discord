from __future__ import annotations

from io import BytesIO
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from bot.services.youtube import YouTubeTrack
from bot.utils.time import format_duration


CARD_WIDTH = 1130
CARD_PADDING = 28
TITLE_HEIGHT = 72
COLUMN_GAP = 18
ITEM_GAP = 10
ITEM_HEIGHT = 82
THUMBNAIL_SIZE = 54
MAX_TRACKS = 10
THUMBNAIL_TIMEOUT = 4

BACKGROUND_TOP = (255, 241, 214)
BACKGROUND_MIDDLE = (255, 211, 110)
BACKGROUND_BOTTOM = (255, 159, 104)
CARD_FILL = (255, 248, 234)
CARD_ALT_FILL = (255, 255, 255)
CARD_BORDER = (255, 179, 71)
BADGE_FILL = (255, 107, 94)
TEXT_MAIN = (43, 43, 43)
TEXT_SECONDARY = (93, 169, 233)
ACCENT = (126, 217, 87)
WHITE = (255, 255, 255)


def build_music_search_card(query: str, tracks: list[YouTubeTrack]) -> BytesIO:
    visible_tracks = tracks[:MAX_TRACKS]
    rows = max(1, (len(visible_tracks) + 1) // 2)
    height = CARD_PADDING * 2 + TITLE_HEIGHT + rows * ITEM_HEIGHT + (rows - 1) * ITEM_GAP

    image = Image.new("RGB", (CARD_WIDTH, height), BACKGROUND_TOP)
    _draw_gradient(image)

    draw = ImageDraw.Draw(image)
    title_font = _load_font(30, bold=True)
    item_font = _load_font(22, bold=True)
    meta_font = _load_font(17)
    number_font = _load_font(20, bold=True)

    title = _fit_text(draw, query.strip() or "Search", title_font, CARD_WIDTH - CARD_PADDING * 2)
    title_width = _text_width(draw, title, title_font)
    title_x = (CARD_WIDTH - title_width) // 2
    draw.text((title_x + 2, CARD_PADDING + 2), title, fill=(255, 179, 71), font=title_font)
    draw.text((title_x, CARD_PADDING), title, fill=TEXT_MAIN, font=title_font)

    underline_width = 70
    underline_x = (CARD_WIDTH - underline_width) // 2
    draw.rounded_rectangle(
        (underline_x, CARD_PADDING + 44, underline_x + underline_width, CARD_PADDING + 49),
        radius=3,
        fill=ACCENT,
    )
    _draw_crayon_accents(draw, CARD_WIDTH, height)

    column_width = (CARD_WIDTH - CARD_PADDING * 2 - COLUMN_GAP) // 2
    left_tracks = visible_tracks[:5]
    right_tracks = visible_tracks[5:]
    start_y = CARD_PADDING + TITLE_HEIGHT

    for column_index, column_tracks in enumerate((left_tracks, right_tracks)):
        x = CARD_PADDING + column_index * (column_width + COLUMN_GAP)
        for row_index, track in enumerate(column_tracks):
            y = start_y + row_index * (ITEM_HEIGHT + ITEM_GAP)
            _draw_track_item(
                draw,
                image,
                track,
                index=column_index * 5 + row_index + 1,
                x=x,
                y=y,
                width=column_width,
                item_font=item_font,
                meta_font=meta_font,
                number_font=number_font,
            )

    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


def _draw_gradient(image: Image.Image) -> None:
    width, height = image.size
    gradient = Image.new("RGB", (2, 2))
    gradient.putdata(
        [
            BACKGROUND_TOP,
            BACKGROUND_MIDDLE,
            BACKGROUND_MIDDLE,
            BACKGROUND_BOTTOM,
        ]
    )
    image.paste(gradient.resize((width, height), Image.Resampling.BILINEAR))


def _draw_crayon_accents(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    accent_shapes = [
        (34, 24, 124, 42, ACCENT),
        (width - 160, 34, width - 54, 52, BADGE_FILL),
        (48, height - 38, 144, height - 20, TEXT_SECONDARY),
        (width - 132, height - 44, width - 48, height - 26, ACCENT),
    ]
    for x1, y1, x2, y2, color in accent_shapes:
        draw.arc((x1, y1, x2, y2), start=8, end=172, fill=color, width=5)

    for x, y, color in (
        (180, 30, BADGE_FILL),
        (width - 210, 78, ACCENT),
        (78, height - 82, TEXT_SECONDARY),
        (width - 64, height - 90, CARD_BORDER),
    ):
        draw.ellipse((x, y, x + 10, y + 10), fill=color)


def _draw_track_item(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    track: YouTubeTrack,
    *,
    index: int,
    x: int,
    y: int,
    width: int,
    item_font: ImageFont.ImageFont,
    meta_font: ImageFont.ImageFont,
    number_font: ImageFont.ImageFont,
) -> None:
    radius = 14
    fill = CARD_FILL if index % 2 else CARD_ALT_FILL
    shadow_offset = 4
    draw.rounded_rectangle(
        (x + shadow_offset, y + shadow_offset, x + width + shadow_offset, y + ITEM_HEIGHT + shadow_offset),
        radius=radius,
        fill=(244, 139, 83),
    )
    draw.rounded_rectangle(
        (x, y, x + width, y + ITEM_HEIGHT),
        radius=radius,
        fill=fill,
        outline=CARD_BORDER,
        width=3,
    )
    draw.line((x + 18, y + 8, x + width - 24, y + 8), fill=(255, 226, 170), width=2)

    badge_size = 34
    badge_x = x + 14
    badge_y = y + (ITEM_HEIGHT - badge_size) // 2
    draw.ellipse(
        (badge_x, badge_y, badge_x + badge_size, badge_y + badge_size),
        fill=BADGE_FILL,
        outline=TEXT_MAIN,
        width=2,
    )

    index_text = str(index)
    index_bbox = draw.textbbox((0, 0), index_text, font=number_font)
    draw.text(
        (
            badge_x + (badge_size - (index_bbox[2] - index_bbox[0])) / 2,
            badge_y + (badge_size - (index_bbox[3] - index_bbox[1])) / 2 - 1,
        ),
        index_text,
        fill=WHITE,
        font=number_font,
    )

    thumb_x = badge_x + badge_size + 14
    thumb_y = y + (ITEM_HEIGHT - THUMBNAIL_SIZE) // 2
    thumbnail = _load_thumbnail(track.thumbnail_url)
    image.paste(thumbnail, (thumb_x, thumb_y), thumbnail)

    text_x = thumb_x + THUMBNAIL_SIZE + 14
    text_width = x + width - text_x - 14
    title = _fit_text(draw, track.title or "Unknown title", item_font, text_width)
    meta = f"{format_duration(track.duration)} - {track.source or track.uploader or 'YouTube'}"
    meta = _fit_text(draw, meta, meta_font, text_width)

    draw.text((text_x, y + 18), title, fill=TEXT_MAIN, font=item_font)
    draw.text((text_x, y + 47), meta, fill=TEXT_SECONDARY, font=meta_font)


def _load_thumbnail(url: str | None) -> Image.Image:
    if url:
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=THUMBNAIL_TIMEOUT) as response:
                data = response.read()
            thumb = Image.open(BytesIO(data)).convert("RGBA")
            return _circle_crop(thumb)
        except (OSError, ValueError, UnidentifiedImageError):
            pass

    fallback = Image.new("RGBA", (THUMBNAIL_SIZE, THUMBNAIL_SIZE), (0, 0, 0, 0))
    fallback_draw = ImageDraw.Draw(fallback)
    fallback_draw.ellipse(
        (0, 0, THUMBNAIL_SIZE - 1, THUMBNAIL_SIZE - 1),
        fill=(*ACCENT, 255),
        outline=(*CARD_BORDER, 255),
        width=2,
    )
    fallback_draw.polygon(
        [
            (THUMBNAIL_SIZE // 2 - 6, THUMBNAIL_SIZE // 2 - 10),
            (THUMBNAIL_SIZE // 2 - 6, THUMBNAIL_SIZE // 2 + 10),
            (THUMBNAIL_SIZE // 2 + 12, THUMBNAIL_SIZE // 2),
        ],
        fill=(*TEXT_MAIN, 235),
    )
    return fallback


def _circle_crop(image: Image.Image) -> Image.Image:
    thumb = image.resize((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
    mask = Image.new("L", (THUMBNAIL_SIZE, THUMBNAIL_SIZE), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, THUMBNAIL_SIZE - 1, THUMBNAIL_SIZE - 1), fill=255)
    output = Image.new("RGBA", (THUMBNAIL_SIZE, THUMBNAIL_SIZE), (0, 0, 0, 0))
    output.paste(thumb, (0, 0), mask)
    return output


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    text = " ".join(str(text).split())
    if _text_width(draw, text, font) <= max_width:
        return text

    ellipsis = "..."
    while text and _text_width(draw, f"{text}{ellipsis}", font) > max_width:
        text = text[:-1]
    return f"{text.rstrip()}{ellipsis}" if text else ellipsis


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()
