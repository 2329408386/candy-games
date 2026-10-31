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
    legacy_html_routes: bool = False
    game_grades: dict = field(default_factory=dict)

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
SITES = {"chinese": CHINESE, "math": MATH}
