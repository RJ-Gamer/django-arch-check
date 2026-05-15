from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("django-arch-check")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"
