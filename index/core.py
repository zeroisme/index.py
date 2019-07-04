import os
import sys
import json
import importlib

from starlette.applications import Starlette
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.responses import RedirectResponse

from .config import Config, logger
from .responses import automatic
from .errors import Http404, Http500
from .watchdog import MonitorFile

config = Config()

sys.path.insert(0, config.path)

app = Starlette(debug=config.DEBUG)

monitorfile = MonitorFile()

os.makedirs(os.path.join(config.path, "statics"), exist_ok=True)
os.makedirs(os.path.join(config.path, "templates"), exist_ok=True)

templates = Jinja2Templates(directory='templates')
app.mount('/static', StaticFiles(directory="statics"))


@app.route("/")
def index(request):
    return RedirectResponse("/index.py", status_code=301)


@app.route('/favicon.ico')
def favicon(request):
    return RedirectResponse("/static/favicon.ico")


@app.route("/{filepath:path}.py", methods=['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'])
def http(request):
    filepath = request.path_params['filepath']
    # Google SEO
    if "_" in filepath:
        return RedirectResponse(filepath.replace("_", "-"), status_code=301)

    filepath = filepath.strip(".").replace("-", "_")
    # judge python file
    abspath = os.path.join(config.path, "main", filepath + ".py")
    if not os.path.exists(abspath):
        raise Http404()

    pathlist = ['main'] + filepath.split("/")

    # find http handler
    module_path = ".".join(pathlist)
    module = importlib.import_module(module_path)
    try:
        get_response = module.HTTP()
    except AttributeError:
        raise Http404()

    # call middleware
    for deep in range(len(pathlist), 0, -1):
        try:
            module = importlib.import_module(".".join(pathlist[:deep]))
            logger.debug(f"Call middleware in {module}")
            get_response = module.Middleware(get_response)
        except AttributeError:
            continue

    # get response
    resp = get_response(request)
    if not isinstance(resp, tuple):
        resp = (resp,)
    return automatic(*resp)
