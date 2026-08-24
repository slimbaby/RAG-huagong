# Pre-patch nest_asyncio before any library imports it.
# uvloop (used by uvicorn) is incompatible with nest_asyncio.apply().
# The mock only protects the import; actual RAGAS evaluation runs in a subprocess.
import sys
import types

if "nest_asyncio" not in sys.modules:
    _mock = types.ModuleType("nest_asyncio")
    _mock.apply = lambda *a, **kw: None
    sys.modules["nest_asyncio"] = _mock
