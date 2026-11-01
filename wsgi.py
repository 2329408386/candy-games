from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix
from core.app import create_app
from sites import CHINESE, MATH, ENGLISH

chinese = create_app(CHINESE)
math = create_app(MATH)
english = create_app(ENGLISH)
# Chinese owns "/", math is mounted at "/math" and English at "/english" (SCRIPT_NAME).
app = ProxyFix(DispatcherMiddleware(chinese, {"/math": math, "/english": english}), x_for=1, x_proto=1)
