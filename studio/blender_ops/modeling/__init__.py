"""Generic, data-driven parametric modelling for studio-v1 subject specs.

All builders take (name, params) with numbers in metres and return a linked
mesh object. No subject-specific code: a train nose, hull, wheel, drum, pipe or
wing all come from the same functions. World axes: X width/span, Y length/
forward, Z up. See each module docstring for the full parameter contract.
"""
from .assemble import build_part, build_subject, mesh_hash, rebuild_parts, relation_order, remove_subject, scene_parts
from .details import clear_canopy, panel_lines
from .loft import loft
from .primitives import box, mesh_object, skin
from .profile import i_section, profile_extrude
from .revolve import revolve
from .sweep import sweep
from .wall import wall
from .wing import airfoil_ring, wing

__all__ = ['airfoil_ring', 'box', 'build_part', 'build_subject', 'clear_canopy', 'i_section', 'loft', 'mesh_hash',
           'mesh_object', 'panel_lines', 'profile_extrude', 'rebuild_parts', 'relation_order', 'remove_subject', 'revolve',
           'scene_parts', 'skin', 'sweep', 'wall', 'wing']
