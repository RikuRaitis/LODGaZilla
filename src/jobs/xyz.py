"""Cartesian 3D data loader using the common grouped-card coordinator."""

from src.data.xyz import load_xyz_data
from src.jobs.plot_groups import GroupedPlotLoadCoordinator


class XYZLoadCoordinator(GroupedPlotLoadCoordinator):
    def __init__(self, parent):
        super().__init__(parent, lambda axes, **kwargs: load_xyz_data(axes, **kwargs))
