"""run tools on this computer for the models (calculations, Lean, tests, git, media), find and install the ones that are missing (with the owner's approval), and keep a record of every run"""
from .toolbox import ToolBox
from .installer import Installer


def build(data_dir, allowed_roots=(), work_root=None, now=None):
    import time
    box = ToolBox(data_dir, allowed_roots, work_root, now or time.time)
    box.installer = Installer(box)
    return box
