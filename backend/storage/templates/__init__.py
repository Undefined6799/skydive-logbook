"""Package marker for app-shipped templates.

Files in this directory are shipped with the app and loaded via
``importlib.resources.files("backend.storage.templates")``. Keeping them
as a proper package (with this ``__init__.py``) rather than a namespace
package is the safest choice across install modes — source, wheel, and
future PyInstaller bundles.
"""
