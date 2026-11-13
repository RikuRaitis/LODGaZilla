@setlocal
@rem Find the code from any start folder; the current folder stays as it is.
@set "PYTHONPATH=%~dp0;%PYTHONPATH%"
@python -m src.app.lodgazilla %*
