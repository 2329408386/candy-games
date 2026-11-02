from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix
from core.app import create_app
from sites import CHINESE, MATH, ENGLISH, TEACHER

chinese = create_app(CHINESE)
math = create_app(MATH)
english = create_app(ENGLISH)
teacher = create_app(TEACHER)
# Chinese owns "/", math is mounted at "/math", English at "/english" and 语文老师工作台 at "/teacher" (SCRIPT_NAME).
app = ProxyFix(
    DispatcherMiddleware(chinese, {"/math": math, "/english": english, "/teacher": teacher}), x_for=1, x_proto=1
)
