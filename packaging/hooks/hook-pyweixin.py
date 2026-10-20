"""Keep the unmodified LGPL library as replaceable Python source, outside PYZ."""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files('pyweixin')
hiddenimports = collect_submodules('pyweixin')
module_collection_mode = 'py'
