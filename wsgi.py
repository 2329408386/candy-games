from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import redirect
from core.app import create_app
from sites import CHINESE, MATH, ENGLISH, TEACHER

chinese = create_app(CHINESE)
math = create_app(MATH)
english = create_app(ENGLISH)
teacher = create_app(TEACHER)
WORKBENCH = "/workspace/chinese"


def moved(environ, start_response):
    """/teacher/… (the workbench's first address) and a bare /workspace lead to the workbench at /workspace/chinese/.
    Back-office links keep their path; the old page addresses changed shape, so they go to the entry page."""
    path = environ.get("PATH_INFO", "")
    old_admin = environ.get("SCRIPT_NAME") == "/teacher" and path.startswith("/admin")
    target = WORKBENCH + (path if old_admin else "/")
    query = environ.get("QUERY_STRING")
    return redirect(target + ("?" + query if query and old_admin else ""), 301)(environ, start_response)


# Chinese owns "/", math is mounted at "/math", English at "/english" and 语文老师工作台 at "/workspace/chinese"
# (SCRIPT_NAME). Longer prefixes win, so "/workspace" only catches what is not the workbench itself.
app = ProxyFix(
    DispatcherMiddleware(
        chinese, {"/math": math, "/english": english, WORKBENCH: teacher, "/workspace": moved, "/teacher": moved}
    ),
    x_for=1,
    x_proto=1,
)
