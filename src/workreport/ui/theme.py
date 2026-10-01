"""디자인 시스템: 색 토큰(라이트/다크), 전역 스타일시트, 폰트(Pretendard), 테마 전환.

- 모든 화면은 여기의 토큰만 쓴다. 직접 그리는 위젯은 paintEvent 에서 tokens() 를 읽고,
  테마가 바뀌면 manager().changed 신호로 다시 그린다.
- 시스템(Windows) 라이트/다크 설정을 따라가며, 설정에서 고정할 수도 있다.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).with_name("assets") / "fonts"
FONT_FAMILY = "Pretendard"
FALLBACK_FAMILIES = ["Pretendard", "Segoe UI Variable Text", "Segoe UI", "Malgun Gothic", "Noto Sans CJK KR", "Apple SD Gothic Neo"]


@dataclass(frozen=True)
class Tokens:
    dark: bool
    bg: str
    surface: str
    surface2: str
    surface3: str
    border: str
    border_strong: str
    text: str
    text2: str
    text3: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_subtle: str
    accent_text: str
    on_accent: str
    success: str
    success_subtle: str
    warning: str
    warning_subtle: str
    danger: str
    danger_hover: str
    danger_subtle: str
    meeting: str
    sidebar: str
    selection: str
    series: tuple[str, ...]


LIGHT = Tokens(
    dark=False,
    bg="#F4F5F7",
    surface="#FFFFFF",
    surface2="#F2F4F7",
    surface3="#E6E9EF",
    border="#E4E7EC",
    border_strong="#D0D5DD",
    text="#101828",
    text2="#475467",
    text3="#98A2B3",
    accent="#2F6BFF",
    accent_hover="#255BE6",
    accent_pressed="#1D4CC7",
    accent_subtle="#EDF2FF",
    accent_text="#2455D6",
    on_accent="#FFFFFF",
    success="#12B76A",
    success_subtle="#E8F8F0",
    warning="#DC6803",
    warning_subtle="#FEF4E6",
    danger="#E5484D",
    danger_hover="#CE2C31",
    danger_subtle="#FDECEC",
    meeting="#7A5AF8",
    sidebar="#FAFAFB",
    selection="#D6E2FF",
    series=("#2F6BFF", "#12B76A", "#F79009", "#EE46BC", "#06AED4", "#F04438", "#66C61C", "#875BF7"),
)

DARK = Tokens(
    dark=True,
    bg="#0D0F13",
    surface="#15181E",
    surface2="#1C2028",
    surface3="#272C36",
    border="#242933",
    border_strong="#343B47",
    text="#ECEEF2",
    text2="#A3ABB8",
    text3="#697284",
    accent="#5B8CFF",
    accent_hover="#7AA2FF",
    accent_pressed="#4A78E6",
    accent_subtle="#18233D",
    accent_text="#93B2FF",
    on_accent="#FFFFFF",
    success="#3CCB7F",
    success_subtle="#11281D",
    warning="#F5A524",
    warning_subtle="#2C2112",
    danger="#FF6369",
    danger_hover="#FF858A",
    danger_subtle="#2E1618",
    meeting="#9E8CFC",
    sidebar="#111318",
    selection="#2A3B66",
    series=("#5B8CFF", "#3CCB7F", "#F5A524", "#F368C9", "#22C3E6", "#FF6B5F", "#8BD443", "#A48AFB"),
)


class ThemeManager(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.mode = "system"  # system | light | dark
        self.tokens = LIGHT
        self._watching = False


_manager: ThemeManager | None = None


def manager() -> ThemeManager:
    global _manager
    if _manager is None:
        _manager = ThemeManager()
    return _manager


def tokens() -> Tokens:
    return manager().tokens


def qcolor(value: str, alpha: int | None = None) -> QColor:
    color = QColor(value)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


# ---------------------------------------------------------------- 폰트


def load_fonts() -> str:
    """번들 Pretendard 를 등록하고 앱 기본 글꼴로 쓸 패밀리 이름을 돌려준다."""
    loaded = False
    for path in sorted(FONT_DIR.glob("*.otf")):
        if QFontDatabase.addApplicationFont(str(path)) >= 0:
            loaded = True
    if not loaded:
        log.warning("Pretendard 글꼴을 불러오지 못해 시스템 글꼴을 씁니다.")
    return FONT_FAMILY if loaded else ""


def app_font(size_pt: float = 9.75) -> QFont:
    font = QFont()
    font.setFamilies(FALLBACK_FAMILIES)
    font.setPointSizeF(size_pt)
    font.setHintingPreference(QFont.PreferNoHinting)
    return font


def font(size_px: int, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont()
    f.setFamilies(FALLBACK_FAMILIES)
    f.setPixelSize(size_px)
    f.setWeight(weight)
    return f


# ---------------------------------------------------------------- 스타일시트


def _asset_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "workreport-ui"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _icon_png(name: str, color: str, size: int = 16) -> str:
    """QSS 에서 쓸 아이콘 PNG 를 만들어 경로를 돌려준다(체크 표시, 콤보 화살표 등)."""
    from .icons import render_pixmap

    path = _asset_dir() / f"{name}-{color.lstrip('#')}-{size}.png"
    if not path.exists():
        render_pixmap(name, color, size, ratio=2.0).save(str(path))
    return path.as_posix()


def stylesheet(t: Tokens) -> str:
    check = _icon_png("check", t.on_accent, 14)
    chevron = _icon_png("chevron-down", t.text2, 14)
    return f"""
* {{ outline: none; }}
QWidget {{ color: {t.text}; }}
QMainWindow, QDialog, #Root, #Page {{ background: {t.bg}; }}
QLabel {{ background: transparent; }}

/* ---------- 사이드바 ---------- */
#Sidebar {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
QToolButton#NavButton {{
    text-align: left; padding: 9px 12px; border: none; border-radius: 8px;
    color: {t.text2}; background: transparent; font-weight: 500;
}}
QToolButton#NavButton:hover {{ background: {t.surface2}; color: {t.text}; }}
QToolButton#NavButton:checked {{ background: {t.accent_subtle}; color: {t.accent_text}; font-weight: 600; }}
#SidebarStatus {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}

/* ---------- 카드·텍스트 ---------- */
QFrame#Card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 14px; }}
QFrame#SubtleCard {{ background: {t.surface2}; border: 1px solid {t.border}; border-radius: 12px; }}
QFrame#AccentCard {{ background: {t.accent_subtle}; border: 1px solid {t.selection}; border-radius: 14px; }}
QFrame#DangerCard {{ background: {t.danger_subtle}; border: 1px solid {t.danger_subtle}; border-radius: 12px; }}
QFrame#ItemCard {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; }}
QFrame#ItemCard:hover {{ border-color: {t.border_strong}; }}
QFrame#ChoiceCard {{ background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 12px; }}
QFrame#ChoiceCard:hover {{ border-color: {t.text3}; }}
QFrame#ChoiceCard[selected="true"] {{ background: {t.accent_subtle}; border: 2px solid {t.accent}; }}
QFrame#Divider {{ background: {t.border}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#SaveBar {{ background: {t.surface}; border-top: 1px solid {t.border}; }}
QFrame#Toast {{ background: {"#2A2F3A" if t.dark else "#1D2433"}; border-radius: 10px; }}
QLabel#ToastText {{ color: #FFFFFF; font-weight: 500; }}

QLabel[role="title"] {{ font-size: 24px; font-weight: 700; }}
QLabel[role="subtitle"] {{ color: {t.text2}; font-size: 13px; }}
QLabel[role="section"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="caption"] {{ color: {t.text3}; font-size: 12px; }}
QLabel[role="muted"] {{ color: {t.text2}; }}
QLabel[role="stat"] {{ font-size: 24px; font-weight: 700; }}
QLabel[role="statlabel"] {{ color: {t.text2}; font-size: 12px; font-weight: 500; }}
QLabel[role="brand"] {{ font-size: 15px; font-weight: 700; }}
QLabel[role="emptytitle"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="link"] {{ color: {t.accent_text}; }}

QLabel[chip="neutral"] {{ background: {t.surface2}; color: {t.text2}; border: 1px solid {t.border}; border-radius: 10px; padding: 3px 9px; font-size: 12px; font-weight: 600; }}
QLabel[chip="accent"] {{ background: {t.accent_subtle}; color: {t.accent_text}; border-radius: 10px; padding: 3px 9px; font-size: 12px; font-weight: 600; }}
QLabel[chip="success"] {{ background: {t.success_subtle}; color: {t.success}; border-radius: 10px; padding: 3px 9px; font-size: 12px; font-weight: 600; }}
QLabel[chip="warning"] {{ background: {t.warning_subtle}; color: {t.warning}; border-radius: 10px; padding: 3px 9px; font-size: 12px; font-weight: 600; }}
QLabel[chip="danger"] {{ background: {t.danger_subtle}; color: {t.danger}; border-radius: 10px; padding: 3px 9px; font-size: 12px; font-weight: 600; }}
QLabel[chip="meeting"] {{ background: {t.accent_subtle}; color: {t.meeting}; border-radius: 10px; padding: 3px 9px; font-size: 12px; font-weight: 600; }}

/* ---------- 버튼 ---------- */
QPushButton {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
    border-radius: 8px; padding: 7px 14px; font-weight: 500;
}}
QPushButton:hover {{ background: {t.surface2}; }}
QPushButton:pressed {{ background: {t.surface3}; }}
QPushButton:disabled {{ color: {t.text3}; background: {t.surface2}; border-color: {t.border}; }}
QPushButton[variant="primary"] {{ background: {t.accent}; color: {t.on_accent}; border: 1px solid {t.accent}; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {t.accent_pressed}; }}
QPushButton[variant="primary"]:disabled {{ background: {t.surface3}; border-color: {t.surface3}; color: {t.text3}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {t.text2}; }}
QPushButton[variant="ghost"]:hover {{ background: {t.surface2}; color: {t.text}; }}
QPushButton[variant="danger"] {{ background: {t.danger}; color: #FFFFFF; border: 1px solid {t.danger}; font-weight: 600; }}
QPushButton[variant="danger"]:hover {{ background: {t.danger_hover}; border-color: {t.danger_hover}; }}
QPushButton[variant="record"] {{ background: {t.surface}; color: {t.danger}; border: 1px solid {t.danger}; font-weight: 600; }}
QPushButton[variant="record"]:hover {{ background: {t.danger_subtle}; }}
QPushButton[variant="recording"] {{ background: {t.danger}; color: #FFFFFF; border: 1px solid {t.danger}; font-weight: 700; }}
QPushButton[variant="recording"]:hover {{ background: {t.danger_hover}; }}
QPushButton[variant="chip"] {{
    background: transparent; color: {t.text3}; border: 1px solid {t.border}; border-radius: 11px;
    padding: 2px 9px; font-size: 12px; font-weight: 500;
}}
QPushButton[variant="chip"]:checked {{ background: {t.accent_subtle}; color: {t.accent_text}; border-color: {t.selection}; }}
QPushButton[variant="segment"] {{
    background: transparent; border: none; border-radius: 7px; padding: 6px 14px; color: {t.text2}; font-weight: 500;
}}
QPushButton[variant="segment"]:hover {{ color: {t.text}; }}
QPushButton[variant="segment"]:checked {{ background: {t.surface}; color: {t.text}; font-weight: 600; }}
#Segmented {{ background: {t.surface3 if t.dark else t.surface2}; border-radius: 9px; }}
QPushButton[variant="choice"] {{
    text-align: left; background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 12px; padding: 14px 16px;
}}
QPushButton[variant="choice"]:checked {{ border: 2px solid {t.accent}; background: {t.accent_subtle}; }}
QPushButton[variant="link"] {{ background: transparent; border: none; color: {t.accent_text}; padding: 0; font-weight: 500; }}
QPushButton[variant="link"]:hover {{ text-decoration: underline; }}

QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 5px; color: {t.text2}; }}
QToolButton:hover {{ background: {t.surface2}; }}
QToolButton:pressed {{ background: {t.surface3}; }}
QToolButton:disabled {{ color: {t.text3}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* ---------- 입력 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QTimeEdit, QDateEdit, QComboBox {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {t.selection}; selection-color: {t.text};
}}
QPlainTextEdit, QTextEdit {{ padding: 6px 8px; }}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QTimeEdit:hover, QDateEdit:hover, QComboBox:hover {{ border-color: {t.text3}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QTimeEdit:focus, QDateEdit:focus, QComboBox:focus {{
    border: 1px solid {t.accent};
}}
QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {t.text3}; background: {t.surface2}; }}
QLineEdit[flat="true"], QPlainTextEdit[flat="true"], QSpinBox[flat="true"] {{ background: transparent; border: 1px solid transparent; padding: 3px 6px; }}
QLineEdit[flat="true"]:hover, QPlainTextEdit[flat="true"]:hover, QSpinBox[flat="true"]:hover {{ background: {t.surface2}; border-color: transparent; }}
QLineEdit[flat="true"]:focus, QPlainTextEdit[flat="true"]:focus, QSpinBox[flat="true"]:focus {{ background: {t.surface}; border: 1px solid {t.accent}; }}
QSpinBox[tone="muted"] {{ color: {t.text2}; }}
QLineEdit[level="title"] {{ font-size: 22px; font-weight: 700; }}
QLineEdit[level="heading"] {{ font-size: 16px; font-weight: 600; }}
QLineEdit[level="item"] {{ font-size: 14px; font-weight: 600; }}
QLineEdit[tone="muted"], QPlainTextEdit[tone="muted"] {{ color: {t.text2}; }}
QLineEdit[done="true"] {{ color: {t.text3}; text-decoration: line-through; }}
QSpinBox, QTimeEdit, QDateEdit {{ padding-right: 10px; }}
QSpinBox::up-button, QSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button, QDateEdit::up-button, QDateEdit::down-button {{ width: 0; border: none; }}
QDateEdit::drop-down {{ border: none; width: 22px; }}
QDateEdit::down-arrow, QComboBox::down-arrow {{ image: url("{chevron}"); width: 14px; height: 14px; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; padding: 4px;
    selection-background-color: {t.accent_subtle}; selection-color: {t.text}; outline: none;
}}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1.5px solid {t.border_strong}; background: {t.surface}; }}
QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url("{check}"); }}
QRadioButton {{ spacing: 8px; background: transparent; }}

/* ---------- 목록·스크롤 ---------- */
QScrollArea {{ background: transparent; border: none; }}
QAbstractScrollArea#Transparent, QAbstractScrollArea#Transparent > QWidget {{ background: transparent; }}
QListWidget, QListView {{ background: transparent; border: none; }}
QListWidget::item {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.surface3}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {t.border_strong}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.surface3}; border-radius: 3px; min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QSplitter::handle {{ background: transparent; }}

QProgressBar {{ background: {t.surface3}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
QSlider::groove:horizontal {{ height: 4px; background: {t.surface3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.surface}; border: 2px solid {t.accent}; width: 12px; height: 12px; margin: -6px 0; border-radius: 8px; }}

QToolTip {{ background: {"#2A2F3A" if t.dark else "#1D2433"}; color: #FFFFFF; border: none; padding: 6px 9px; border-radius: 6px; }}
QMenu {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px 7px 12px; border-radius: 6px; color: {t.text}; }}
QMenu::item:selected {{ background: {t.surface2}; }}
QMenu::item:disabled {{ color: {t.text3}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 8px; }}

QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {t.surface}; padding: 4px; }}
QCalendarWidget QToolButton {{ color: {t.text}; font-weight: 600; padding: 4px 10px; border-radius: 6px; }}
QCalendarWidget QToolButton:hover {{ background: {t.surface2}; }}
QCalendarWidget QToolButton::menu-indicator {{ image: none; }}
QCalendarWidget QSpinBox {{ border: none; background: transparent; }}
QCalendarWidget QAbstractItemView {{
    background: {t.surface}; color: {t.text}; selection-background-color: {t.accent}; selection-color: {t.on_accent};
    outline: none; border: none;
}}
QCalendarWidget QAbstractItemView:disabled {{ color: {t.text3}; }}
QTextBrowser {{ background: transparent; border: none; }}
QMessageBox QLabel {{ color: {t.text}; }}
"""


def palette(t: Tokens) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.Window: t.bg,
        QPalette.WindowText: t.text,
        QPalette.Base: t.surface,
        QPalette.AlternateBase: t.surface2,
        QPalette.Text: t.text,
        QPalette.Button: t.surface,
        QPalette.ButtonText: t.text,
        QPalette.Highlight: t.accent,
        QPalette.HighlightedText: t.on_accent,
        QPalette.ToolTipBase: t.surface,
        QPalette.ToolTipText: t.text,
        QPalette.PlaceholderText: t.text3,
        QPalette.Link: t.accent_text,
        QPalette.Mid: t.border,
        QPalette.Midlight: t.surface2,
        QPalette.Dark: t.border_strong,
        QPalette.Light: t.surface,
        QPalette.Shadow: t.border_strong,
    }
    for role, value in roles.items():
        p.setColor(role, QColor(value))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(t.text3))
    return p


# ---------------------------------------------------------------- 적용


def _system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def resolve_dark(mode: str) -> bool:
    if mode == "dark":
        return True
    if mode == "light":
        return False
    return _system_is_dark()


def apply_theme(app: QApplication, mode: str = "system") -> Tokens:
    mgr = manager()
    mgr.mode = mode
    t = DARK if resolve_dark(mode) else LIGHT
    mgr.tokens = t
    app.setStyle("Fusion")
    app.setPalette(palette(t))
    app.setStyleSheet(stylesheet(t))
    if not mgr._watching:
        try:
            app.styleHints().colorSchemeChanged.connect(lambda _scheme: mgr.mode == "system" and apply_theme(app, "system"))
            mgr._watching = True
        except AttributeError:
            pass
    mgr.changed.emit()
    return t


def setup_application(app: QApplication, mode: str = "system") -> None:
    load_fonts()
    app.setFont(app_font())
    apply_theme(app, mode)


class _ThemeListener(QObject):
    def __init__(self, parent: QObject, fn) -> None:
        super().__init__(parent)
        self._fn = fn

    @Slot()
    def run(self) -> None:
        self._fn()


def on_theme_change(widget: QObject, fn) -> QObject:
    """테마가 바뀌면 fn 을 부른다. 연결은 widget 이 삭제될 때 함께 끊긴다."""
    listener = _ThemeListener(widget, fn)
    manager().changed.connect(listener.run)
    return listener


def repolish(widget) -> None:
    """동적 속성(variant, chip 등)을 바꾼 뒤 스타일을 다시 적용한다."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
