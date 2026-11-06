from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Site:
    key: str
    title: str
    cookie_name: str
    secret_env: str
    games: dict  # game id -> display name, in display order
    files: dict  # game id -> HTML file under games/
    admin_hint: str
    admin_scope_header: str
    url: str = "/"
    emoji: str = ""
    legacy_html_routes: bool = False
    game_grades: dict = field(default_factory=dict)
    kind: str = "games"  # "games" (the game sites) or "workbench" (语文老师工作台, core/essay)

    def __post_init__(self):
        if not self.game_grades:
            object.__setattr__(self, "game_grades", {game: "g3-t1" for game in self.games})

    @property
    def game_root(self):
        return ROOT / self.key / "games"

    @property
    def bank_root(self):
        return ROOT / self.key / "banks"

    @property
    def static_root(self):
        return ROOT / self.key / "static"

    @property
    def template_root(self):
        return ROOT / self.key / "templates"


CHINESE = Site(
    key="chinese",
    title="语文小游戏",
    url="/",
    emoji="📖",
    cookie_name="session",
    secret_env="CHINESE_SECRET",
    legacy_html_routes=True,
    games={
        "punctuation": "标点PK",
        "dedide": "的地得PK",
        "reading": "课外阅读PK",
        "sentence": "修改病句PK",
        "polyphone": "多音字PK",
        "similar-char": "形近字PK",
        "passive": "被字句PK",
        "expand": "扩写句子PK",
        "shorten": "缩写句子PK",
        "rhetorical": "反问句改陈述句PK",
        "poetry": "古诗词PK",
    },
    files={
        "punctuation": "标点pk.html",
        "dedide": "的地得pk.html",
        "reading": "课外阅读pk.html",
        "sentence": "修改病句pk.html",
        "polyphone": "多音字练习pk.html",
        "similar-char": "形近字练习pk.html",
        "passive": "改写被字句pk.html",
        "expand": "扩写句子pk.html",
        "shorten": "缩写句子pk.html",
        "rhetorical": "反问句改陈述句pk.html",
        "poetry": "古诗词pk.html",
    },
    admin_hint="按年级分配授权，填写备注后将授权码发送给用户。",
    admin_scope_header="授权年级／学期",
)
MATH = Site(
    key="math",
    title="数学小游戏",
    url="/math/",
    emoji="✏️",
    cookie_name="math_session",
    secret_env="MATH_SECRET",
    games={
        "conversion": "单位换算PK",
        "mental": "口算PK",
        "measure": "量长度PK",
        "units": "长度单位PK",
        "directions": "校园方向PK",
    },
    files={
        "conversion": "unit-conversion.html",
        "mental": "mental-math.html",
        "measure": "measure-length.html",
        "units": "length-units.html",
        "directions": "campus-directions.html",
    },
    admin_hint="按学期和游戏组分配授权，填写备注后将授权码发送给用户。",
    admin_scope_header="授权年级／学期／组",
)
ENGLISH = Site(
    key="english",
    title="英语小游戏",
    url="/english/",
    emoji="🔤",
    cookie_name="english_session",
    secret_env="ENGLISH_SECRET",
    games={
        "listen-word": "听音识词PK",
        "scene": "情景选择PK",
        "letters": "字母奇妙屋",
        "grammar": "句子小侦探",
        "read": "读句子",
    },
    files={
        "listen-word": "listen-word.html",
        "scene": "scene.html",
        "letters": "letters.html",
        "grammar": "grammar.html",
        "read": "read.html",
    },
    admin_hint="按学期和游戏组分配授权，填写备注后将授权码发送给用户。",
    admin_scope_header="授权年级／学期／组",
)
# 语文老师工作台: licenses, devices and the admin come from core/app.py like the game sites; everything else is
# core/essay. It is not in SITES (which lists the game sites with banks and game pages); ALL_SITES has all four.
TEACHER = Site(
    key="teacher",
    title="语文老师工作台",
    url="/workspace/chinese/",
    emoji="📝",
    cookie_name="teacher_session",
    secret_env="TEACHER_SECRET",
    kind="workbench",
    games={"essay": "作文批改"},
    files={},
    game_grades={"essay": "g1-t1"},
    admin_hint="生成老师授权码：勾选功能和年限（从老师激活那天起算），在授权详情里续期、设置学生名额和每月批改篇数。",
    admin_scope_header="授权内容",
)
SITES = {"chinese": CHINESE, "math": MATH, "english": ENGLISH}
ALL_SITES = {**SITES, "teacher": TEACHER}
