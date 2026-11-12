"""Supported folder metadata access for plugins, including transactional writes."""
from src.data.folder_info import FolderInfoData, info_file_path, read_info_file, write_info_file

__all__ = ["FolderInfoData", "info_file_path", "read_info_file", "write_info_file"]
