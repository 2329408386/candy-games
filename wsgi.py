from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix
from core.app import create_app
from sites import CHINESE, MATH

chinese = create_app(CHINESE)
math = create_app(MATH)
# Chinese owns "/", math is mounted at "/math" (SCRIPT_NAME), exactly as nginx exposed them before the merge.
app = ProxyFix(DispatcherMiddleware(chinese, {"/math": math}), x_for=1, x_proto=1)
