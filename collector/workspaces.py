"""Old name of the command centre. The code lives in components/project_management/centre.py.
This alias keeps `import workspaces` working, as the same module object, until step 2B."""
import sys
from pathlib import Path

from core.component import import_folder

sys.modules[__name__] = import_folder(Path(__file__).resolve().parent / "components" / "project_management").centre
