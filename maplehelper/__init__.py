__version__ = "0.10.1"
APP_NAME = "Maple Helper"

# Before anything imports numpy: its OpenBLAS starts a worker thread per CPU core and commits a buffer for each
# (15 idle threads and ~500 MB of commit charge on a 16-core PC), for element-wise work that needs none of them.
# Only OpenBLAS: the speech model (CTranslate2) sizes its own threads from OMP_NUM_THREADS, which stays as it is.
import os as _os  # noqa: E402

_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
