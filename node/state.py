import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import hou
import stateutils as su  # ty: ignore[unresolved-import]
import viewerhandle.utils as hu  # ty: ignore[unresolved-import]
from pxr import Gf, Usd, UsdGeom


def get_state_name(node_type: hou.OpNodeType) -> str:
    """Return the default state name."""

    definition = node_type.definition()
    if definition is None:
        raise RuntimeError('invalid node definition')
    sections = definition.sections()
    state_name = sections['DefaultState'].contents()
    return state_name


NODE_TYPE: hou.OpNodeType = kwargs['type']  # noqa: F821  # ty: ignore[unresolved-reference]
STATE_NAME = get_state_name(NODE_TYPE)
STATE_LABEL = 'Layout'
STATE_CATEGORY = hou.lopNodeTypeCategory()
STATE_MENU = STATE_NAME + '_menu'
HK_CTXT = su.hotkeyContextForState(STATE_NAME, STATE_CATEGORY)

HUD_TEMPLATE = {
    'title': 'Layout',
    'desc': '',
    'icon': 'LOP_edit',
    'rows': [
        {'type': 'divider', 'label': 'Basic'},
        {'label': 'Translate', 'key': 'T'},
        {'label': 'Rotate', 'key': 'R'},
        {'label': 'Scale', 'key': 'E'},
        {'type': 'divider', 'label': 'Transforms'},
        {
            'label': 'Toggle Multi-Snapping',
            'key': su.hudHotkeyRef('h.pane.gview.edit_snap_toggle'),
        },
        {'label': 'Cycle Handle Alignments', 'key': 'M'},
        {'label': 'Cycle Transform Handles', 'key': 'Y'},
        {'label': 'Toggle Handle Pivot', 'key': 'Shift+` or Insert'},
        {'type': 'divider', 'label': 'Selections'},
        {'label': 'Tap or Hold to Select Prims', 'key': 'S'},
        {'type': 'divider', 'label': 'Basic'},
        {'label': 'Duplicate', 'key': 'CTRL + D'},
        {'label': 'Remove Edits', 'key': 'Del'},
    ],
}
DEFAULT_HANDLE_PARMS = {
    'px': 0,
    'py': 0,
    'pz': 0,
    'pivot_rx': 0,
    'pivot_ry': 0,
    'pivot_rz': 0,
    'pivot_comp_tx': 0,
    'pivot_comp_ty': 0,
    'pivot_comp_tz': 0,
    'tx': 0,
    'ty': 0,
    'tz': 0,
    'rx': 0,
    'ry': 0,
    'rz': 0,
    'sx': 1,
    'sy': 1,
    'sz': 1,
}
MAX_ITERATIONS = 1000
TOLERANCE = 1e-5
MAX_VARIANT_SETS = 5
MAX_VARIANTS = 50


@dataclass(frozen=True)
class TransformParms:
    translate: hou.ParmTuple
    rotate: hou.ParmTuple
    scale: hou.ParmTuple

    @classmethod
    def from_node(cls, node: hou.OpNode, index: int | None = None) -> 'TransformParms':
        """Return the transform parameters of a node or of a multi parm block."""

        suffix = '' if index is None else str(index)
        return cls(
            translate=get_parm_tuple(node, f't{suffix}'),
            rotate=get_parm_tuple(node, f'r{suffix}'),
            scale=get_parm_tuple(node, f's{suffix}'),
        )

    def get_xform(self) -> hou.Matrix4:
        """Return the xform from the parameters."""

        transform_dict = {
            'translate': self.translate.evalAsFloats(),
            'rotate': self.rotate.evalAsFloats(),
            'scale': self.scale.evalAsFloats(),
        }
        xform = hou.hmath.buildTransform(transform_dict)
        return xform

    def set_xform(self, xform: hou.Matrix4, sanitize: bool = False) -> None:
        """Set the xform to the parameters."""

        components = xform.explode()
        translate = components['translate']
        rotate = components['rotate']
        scale = components['scale']

        if sanitize:
            translate = [v * (abs(v) > TOLERANCE) for v in translate]
            rotate = [v * (abs(v) > TOLERANCE) for v in rotate]

        self.translate.set([translate[0], translate[1], translate[2]])
        self.rotate.set([rotate[0], rotate[1], rotate[2]])
        self.scale.set([scale[0], scale[1], scale[2]])

    def revert(self) -> None:
        """Revert all parms to defaults."""

        self.translate.revertToDefaults()
        self.rotate.revertToDefaults()
        self.scale.revertToDefaults()

    def copy_from(self, other: 'TransformParms') -> None:
        """Copy the values from other parameters."""

        self.translate.set(other.translate.evalAsFloats())
        self.rotate.set(other.rotate.evalAsFloats())
        self.scale.set(other.scale.evalAsFloats())


@dataclass(frozen=True)
class VariantParms:
    variant_set: hou.Parm
    variant_name: hou.Parm


@dataclass(frozen=True)
class PrimParms:
    """Lazily resolved parameters of a primitive multi parm block."""

    node: hou.LopNode
    # Identifies the block for the current operation only.
    index: int

    def destination(self) -> str:
        return self._parm('destinationprim').evalAsString()

    def set_destination(self, path: str) -> None:
        self._parm('destinationprim').set(path)

    def source(self) -> str:
        return self._parm('sourceprim').evalAsString()

    def set_source(self, path: str) -> None:
        self._parm('sourceprim').set(path)

    def transform(self) -> TransformParms:
        return TransformParms.from_node(self.node, self.index)

    def get_variants(self) -> tuple[VariantParms, ...]:
        count_parm = self._parm('variants')
        offset = count_parm.multiParmStartOffset()

        variants = [
            VariantParms(
                variant_set=self._variant_parm('variantset', offset + i),
                variant_name=self._variant_parm('variantname', offset + i),
            )
            for i in range(count_parm.evalAsInt())
        ]
        return tuple(variants)

    def set_variant(self, variant_set: str, variant_name: str) -> None:
        """Select a variant, reusing the entry of an existing variant set."""

        for variant in self.get_variants():
            if variant.variant_set.evalAsString() == variant_set:
                variant.variant_name.set(variant_name)
                return

        count_parm = self._parm('variants')
        offset = count_parm.multiParmStartOffset()
        count = count_parm.evalAsInt()
        count_parm.set(count + 1)

        variant = VariantParms(
            variant_set=self._variant_parm('variantset', offset + count),
            variant_name=self._variant_parm('variantname', offset + count),
        )
        variant.variant_set.set(variant_set)
        variant.variant_name.set(variant_name)

    def copy_from(self, other: 'PrimParms') -> None:
        """Copy the transforms and variant selections of another primitive."""

        self.transform().copy_from(other.transform())
        for variant in other.get_variants():
            self.set_variant(
                variant.variant_set.evalAsString(),
                variant.variant_name.evalAsString(),
            )

    def _parm(self, name: str) -> hou.Parm:
        return get_parm(self.node, f'{name}{self.index}')

    def _variant_parm(self, name: str, index: int) -> hou.Parm:
        return get_parm(self.node, f'{name}{self.index}_{index}')


class State:
    duplicate_key = f'{HK_CTXT}.duplicate'
    translate_key = f'{HK_CTXT}.translate'
    rotate_key = f'{HK_CTXT}.rotate'
    scale_key = f'{HK_CTXT}.scale'
    delete_key = f'{HK_CTXT}.delete'

    def __init__(
        self, state_name: str, scene_viewer: hou.SceneViewer, **kwargs: dict[str, Any]
    ) -> None:
        self.state_name = state_name
        self.scene_viewer = scene_viewer

        self.xform_handle = hou.Handle(self.scene_viewer, 'Xform')
        self._node: hou.LopNode | None = None

        self._delta_xform: hou.Matrix4 | None = None
        self._temp_parms: TransformParms | None = None
        self._variant_menu_items: dict[str, str] = {}
        self._variant_menu_values: dict[str, str] = {}

    @property
    def node(self) -> hou.LopNode:
        """
        Return the current node.

        :raises RuntimeError: if the state has no node.
        """

        node = self._node
        if node is None:
            raise RuntimeError('state has no node')
        return node

    @staticmethod
    def bind_handles(template: hou.ViewerStateTemplate) -> None:
        """Bind handles to a template."""

        handle_type = 'xform'
        handle_name = 'Xform'
        template.bindHandle(handle_type, handle_name, cache_previous_parms=True)  # ty: ignore[unknown-argument]
        template.bindSupportsSelectionChange(True)

    @staticmethod
    def bind_hotkeys(template: hou.ViewerStateTemplate) -> None:
        """Bind hotkeys to a template."""

        hotkey_definitions = hou.PluginHotkeyDefinitions()

        hotkey_definitions.addCommandCategory(
            HK_CTXT, 'LOP Layout State', 'These keys apply to the LOP Layout State.'
        )
        hotkey_definitions.addContext(
            HK_CTXT, 'LOP Layout State', 'These keys apply to the LOP Layout State.'
        )

        hotkey_definitions.addCommand(State.duplicate_key, 'Duplicate', 'Duplicate')
        hotkey_definitions.addDefaultBinding(HK_CTXT, State.duplicate_key, ['CTRL+D'])

        hotkey_definitions.addCommand(State.translate_key, 'Translate', 'Translate')
        hotkey_definitions.addDefaultBinding(HK_CTXT, State.translate_key, ['T'])

        hotkey_definitions.addCommand(State.rotate_key, 'Rotate', 'Rotate')
        hotkey_definitions.addDefaultBinding(HK_CTXT, State.rotate_key, ['R'])

        hotkey_definitions.addCommand(State.scale_key, 'Scale', 'Scale')
        hotkey_definitions.addDefaultBinding(HK_CTXT, State.scale_key, ['E'])

        hotkey_definitions.addCommand(State.delete_key, 'Remove', 'Remove')
        hotkey_definitions.addDefaultBinding(HK_CTXT, State.delete_key, ['Del'])

        template.bindHotkeyDefinitions(hotkey_definitions)

    @staticmethod
    def bind_menus(template: hou.ViewerStateTemplate) -> None:
        """Bind menus to a template."""

        menu = hou.ViewerStateMenu(STATE_MENU, STATE_LABEL)
        menu.addActionItem('duplicate', 'Duplicate', State.duplicate_key)  # ty: ignore[too-many-positional-arguments]
        menu.addActionItem('remove', 'Remove Edits', State.delete_key)  # ty: ignore[too-many-positional-arguments]

        submenu = hou.ViewerStateMenu('variants', 'Variants')
        menu.addMenu(submenu)
        for i in range(MAX_VARIANT_SETS):
            radio = f'variant_set_{i}'
            submenu.addRadioStrip(radio, f'Variant Set {i}', f'variant_{i}_0')
            for j in range(MAX_VARIANTS):
                submenu.addRadioStripItem(radio, f'variant_{i}_{j}', f'Variant {j}')

        template.bindMenu(menu)

    def onEnter(self, kwargs: dict[str, Any]) -> None:
        """Called when the state is entered from an existing node."""

        self._init_node(kwargs['node'])

        if self._node is None:
            return

        self.scene_viewer.hudInfo(show=True, template=HUD_TEMPLATE)  # ty: ignore[unresolved-attribute]

    def onExit(self, kwargs: dict[str, Any]) -> None:
        """Called when the state is about to be exited."""

        self._node = None
        self._delta_xform = None
        self._temp_parms = None

    def onKeyEvent(self, kwargs: dict[str, Any]) -> bool:
        """Called for key events."""

        hotkey = hu.hotkeySymbolOrKeyString(kwargs)

        if hou.hotkeys.isKeyMatch(hotkey, State.translate_key):
            self.xform_handle.applySettings('translate(1)')
            return True
        elif hou.hotkeys.isKeyMatch(hotkey, State.rotate_key):
            self.xform_handle.applySettings('rotate(1)')
            return True
        elif hou.hotkeys.isKeyMatch(hotkey, State.scale_key):
            self.xform_handle.applySettings('scale(1)')
            return True
        return False

    def onMenuAction(self, kwargs: dict[str, Any]) -> None:
        """Called when a context menu choice is selected."""

        if self._node is None:
            return

        menu_item = cast(str, kwargs.get('menu_item'))
        if menu_item == 'duplicate':
            self._duplicate_primitives()

            # Force a Pivot update manually.
            self.xform_handle.update()

        elif menu_item == 'remove':
            self._remove_primitives()

            # Force a Pivot update manually.
            self.xform_handle.update()

        elif menu_item.startswith('variant_set'):
            menu_item_selection = kwargs.get(menu_item, '')
            variant_set = self._variant_menu_items.get(menu_item)
            variant_name = self._variant_menu_items.get(menu_item_selection)
            if variant_set is not None and variant_name is not None:
                self.scene_viewer.beginStateUndo('Set variant')
                self._update_variant(variant_set, variant_name)
                self.scene_viewer.endStateUndo()

    def onMenuPreOpen(self, kwargs):
        """
        Called when the context menu is about to be shown, to update the state
        of the menu and its items.
        """

        self._update_menu(kwargs)

    def onBeginHandleToState(self, kwargs: dict[str, Any]) -> None:
        """Called at the start of user interaction with a handle."""

        if self._node is None:
            return

        self.scene_viewer.beginStateUndo('Move primitives')

        self._init_transforms()

    def onEndHandleToState(self, kwargs: dict[str, Any]) -> None:
        """Called at the end of user interaction with a handle."""

        if self._node is None:
            return

        self._apply_transforms()

        self.scene_viewer.endStateUndo()

    def onHandleToState(self, kwargs: dict[str, Any]) -> None:
        """Called on user interaction with a bound handle."""

        if self._node is None:
            return

        # NOTE: On hou.uiEventReason.Start the prev_parms are not populated.
        ui_event = kwargs['ui_event']

        if ui_event.reason() == hou.uiEventReason.Active:
            handle = kwargs['handle']
            parms = kwargs['parms']
            previous_parms = kwargs['prev_parms']

            if handle == self.xform_handle.name():
                xform = get_xform_from_parms(parms)
                previous_xform = get_xform_from_parms(previous_parms)
                delta_xform = previous_xform.inverted() * xform
                self._move_primitives(delta_xform)

    def onStateToHandle(self, kwargs: dict[str, Any]) -> None:
        """Called when node parameters change, to update handle parameters."""

        if self._node is None:
            return

        self._update_pivot(kwargs)

    def onSelection(self, kwargs: dict[str, Any]) -> bool:
        """Called when the user selected geometry. Return True to accept the
        selection and stop the selector."""

        if self._node is None:
            return True

        self.xform_handle.update()
        return True

    def _init_node(self, node: hou.OpNode) -> None:
        """Initialize the node."""

        if node.type() != NODE_TYPE:
            return

        self._node = cast(hou.LopNode, node)
        self._delta_xform = None
        self._temp_parms = None

        # Initial mode
        mode = node.cachedUserData('mode')
        if mode is not None:
            node.destroyCachedUserData('mode')
        if mode == 'translate':
            self.xform_handle.applySettings('translate(1)')
        elif mode == 'rotate':
            self.xform_handle.applySettings('rotate(1)')
        elif mode == 'scale':
            self.xform_handle.applySettings('scale(1)')

    def _init_transforms(self) -> None:
        """Initialize the temporary transforms."""

        self._delta_xform = None
        self._get_temp_parms().revert()

        network = get_lop_network(self.node)
        stage = self.node.stage()
        if network is None or stage is None:
            return

        selection = get_selection(network, stage)

        paths = ' '.join(selection)
        get_parm(self.node, 'primpattern').set(paths)

    def _apply_transforms(self) -> None:
        """Apply the temporary transforms to the multi parm."""

        input_node = self.node.node('IN_transform')
        if not isinstance(input_node, hou.LopNode):
            return

        stage = input_node.stage()
        if stage is None:
            return

        temp_parms = self._get_temp_parms()
        delta_xform = temp_parms.get_xform()

        # Don't create an entry without changes.
        if is_identity(delta_xform):
            return

        prim_parm = get_parm(self.node, 'primpattern')
        pattern = prim_parm.evalAsString()

        # Revert internal parms and variables
        prim_parm.revertToDefaults()
        temp_parms.revert()
        self._delta_xform = None

        # Apply transform in local space to parameters
        time_code = Usd.TimeCode(hou.frame())
        cache = UsdGeom.XformCache(time_code)
        parms_map = self._parms_map()
        for path in pattern.split():
            prim = stage.GetPrimAtPath(path)
            if not prim.IsValid():
                continue

            parms = parms_map.get(path)
            if parms is None:
                parms = self._add_primitive(path)
            transform = parms.transform()

            world_matrix_usd = cache.GetLocalToWorldTransform(prim)

            world_matrix = hou.Matrix4(world_matrix_usd)  # ty: ignore[invalid-argument-type]
            local_matrix = world_matrix * delta_xform * world_matrix.inverted()
            previous_xform = transform.get_xform()
            xform = previous_xform * local_matrix
            transform.set_xform(xform, sanitize=True)

    def _move_primitives(self, xform: hou.Matrix4) -> None:
        """Move selected primitives by the xform."""

        # Don't create an entry without changes.
        if is_identity(xform):
            return

        if self._delta_xform is None:
            self._delta_xform = hou.Matrix4(1)

        new_xform = self._delta_xform * xform
        self._delta_xform = new_xform

        temp_parms = self._get_temp_parms()
        temp_parms.set_xform(new_xform)

    def _duplicate_primitives(self) -> None:
        """Duplicate a primitive."""

        network = get_lop_network(self.node)
        stage = self.node.stage()
        if network is None or stage is None:
            return

        self.scene_viewer.beginStateUndo('Duplicate primitives')

        selection = get_selection(network, stage)

        duplicated_paths = []
        for path in selection:
            target_path = get_unique_prim_path(stage, path, duplicated_paths)
            self._add_primitive(path=target_path, source=path)
            duplicated_paths.append(target_path)

        network.setSelection(duplicated_paths)

        self.scene_viewer.endStateUndo()

    def _remove_primitives(self) -> None:
        """Remove the edits for primitives."""

        network = get_lop_network(self.node)
        stage = self.node.stage()
        if network is None or stage is None:
            return

        selection = get_selection(network, stage)

        multi_parm = get_parm(self.node, 'primitives')
        count = multi_parm.evalAsInt()
        offset = multi_parm.multiParmStartOffset()

        self.scene_viewer.beginStateUndo('Remove Edits')

        for i in reversed(range(count)):
            parms = PrimParms(self.node, offset + i)
            if parms.destination() in selection:
                multi_parm.removeMultiParmInstance(i)

        network.setSelection(())

        self.scene_viewer.endStateUndo()

    def _update_pivot(self, kwargs: dict[str, Any]) -> None:
        """Update the handle's pivot to the current selection."""

        network = get_lop_network(self.node)
        stage = self.node.stage()
        if network is None or stage is None:
            return

        selection = get_selection(network, stage)

        if not selection:
            self.xform_handle.show(False)
            return

        # Reset State
        kwargs['parms'].update(DEFAULT_HANDLE_PARMS)

        # Set pivot to selection
        if len(selection) == 1:
            path = selection[0]

            pivot = get_pivot(stage=stage, path=path)
            components = pivot.explode()
            kwargs['parms']['px'] = components['translate'][0]
            kwargs['parms']['py'] = components['translate'][1]
            kwargs['parms']['pz'] = components['translate'][2]
            kwargs['parms']['pivot_rx'] = components['rotate'][0]
            kwargs['parms']['pivot_ry'] = components['rotate'][1]
            kwargs['parms']['pivot_rz'] = components['rotate'][2]
        else:
            center = get_bbox_center(stage=stage, paths=selection)
            kwargs['parms']['px'] = center[0]
            kwargs['parms']['py'] = center[1]
            kwargs['parms']['pz'] = center[2]

        self.xform_handle.show(True)

    def _update_menu(self, kwargs: dict) -> None:
        """Update the context menu with Variants of the selection."""

        menu_name = kwargs['menu']
        menu_states = kwargs['menu_states']
        menu_item_states = kwargs['menu_item_states']

        if menu_name == STATE_MENU:
            self._update_variant_menu_items()
            visible = any(self._variant_menu_items.values())
            menu_item_states['variants']['visible'] = visible

        elif menu_name.startswith('variant'):
            for key, item_state in menu_item_states.items():
                label = self._variant_menu_items.get(key, '')

                item_state['label'] = label
                item_state['visible'] = bool(label)

            if menu_name.startswith('variant_set'):
                menu_states['value'] = self._variant_menu_values.get(menu_name, '')

    def _update_variant(self, variant_set: str, variant_name: str) -> None:
        """Update the variant of the current selected primitive."""

        network = get_lop_network(self.node)
        stage = self.node.stage()
        if network is None or stage is None:
            return

        selection = get_selection(network, stage)
        if not selection:
            return

        path = selection[0]
        parms_map = self._parms_map()
        parms = parms_map.get(path)
        if parms is None:
            parms = self._add_primitive(path)

        parms.set_variant(variant_set, variant_name)

    def _update_variant_menu_items(self) -> None:
        """Update the Variant menu items for a Primitive."""

        # Clear
        for key in self._variant_menu_items:
            self._variant_menu_items[key] = ''
        self._variant_menu_values.clear()

        # Get Selection
        network = get_lop_network(self.node)
        stage = self.node.stage()
        if network is None or stage is None:
            return

        selection = get_selection(network, stage)
        if not selection:
            return

        path = selection[0]
        primitive = stage.GetPrimAtPath(path)

        # Update Variants
        variant_sets = self._get_variant_sets(primitive)
        for i, (variant_set_name, names) in enumerate(variant_sets.items()):
            self._variant_menu_items[f'variant_set_{i}'] = variant_set_name

            selection_name = primitive.GetVariantSet(
                variant_set_name
            ).GetVariantSelection()
            for j, name in enumerate(names):
                self._variant_menu_items[f'variant_{i}_{j}'] = name
                if name == selection_name:
                    self._variant_menu_values[f'variant_set_{i}'] = f'variant_{i}_{j}'

    def _add_primitive(self, path: str, source: str = '') -> PrimParms:
        """Add a primitive to the multi parm."""

        multi_parm = get_parm(self.node, 'primitives')
        offset = multi_parm.multiParmStartOffset()
        count = multi_parm.evalAsInt()
        multi_parm.set(count + 1)

        parms = PrimParms(self.node, offset + count)
        parms.set_destination(path)

        if source:
            parms_map = self._parms_map()
            source_parms = parms_map.get(source)
            source_path = self._get_source_path(source, parms_map)
            parms.set_source(source_path)

            if source_parms is not None:
                parms.copy_from(source_parms)

        return parms

    def _get_source_path(self, path: str, parms_map: dict[str, PrimParms]) -> str:
        """
        Return the source prim path of a primitive in the parms.

        Warning: The `parms_map` parameter is mutable and will be edited.
        """

        if parms := parms_map.get(path):
            source_path = parms.source()
            if source_path:
                parms_map.pop(path, None)
                return self._get_source_path(source_path, parms_map)
        return path

    def _get_temp_parms(self) -> TransformParms:
        """Return the temporary parameters."""

        if self._temp_parms is None:
            self._temp_parms = TransformParms.from_node(self.node)
        return self._temp_parms

    def _get_variant_sets(self, primitive: Usd.Prim) -> dict[str, tuple[str, ...]]:
        """Return the variants for the current selection."""

        variant_sets = {}
        if primitive.IsValid():
            variant_set_names = primitive.GetVariantSets().GetNames()
            for variant_set_name in variant_set_names:
                names = primitive.GetVariantSet(variant_set_name).GetVariantNames()
                variant_sets[variant_set_name] = tuple(names)
        return variant_sets

    def _parms_map(self) -> dict[str, PrimParms]:
        """Return the primitives keyed by destination path."""

        multi_parm = get_parm(self.node, 'primitives')
        offset = multi_parm.multiParmStartOffset()
        count = multi_parm.evalAsInt()

        parms_map: dict[str, PrimParms] = {}
        for i in range(count):
            parms = PrimParms(self.node, offset + i)
            parms_map[parms.destination()] = parms
        return parms_map


def get_parm(node: hou.OpNode, name: str) -> hou.Parm:
    """
    Return a parameter by name.

    :raises RuntimeError: if the parameter does not exist.
    """

    parm = node.parm(name)
    if parm is None:
        raise RuntimeError(f'parameter does not exist: {name}')
    return parm


def get_parm_tuple(node: hou.OpNode, name: str) -> hou.ParmTuple:
    """
    Return a parameter tuple by name.

    :raises RuntimeError: if the parameter tuple does not exist.
    """

    parm_tuple = node.parmTuple(name)
    if parm_tuple is None:
        raise RuntimeError(f'parameter tuple does not exist: {name}')
    return parm_tuple


def get_selection(network: hou.LopNetwork, stage: Usd.Stage) -> tuple[str, ...]:
    """Return a tuple of primitive paths that exist in the stage."""

    selection = network.selection()
    selection = [s for s in selection if stage.GetPrimAtPath(s)]
    return tuple(selection)


def get_bbox_center(stage: Usd.Stage, paths: Sequence[str]) -> Gf.Vec3d:
    """Return the combined bounding box center of a list of primitive paths."""

    time_code = Usd.TimeCode(hou.frame())
    purposes = [UsdGeom.Tokens.default_, UsdGeom.Tokens.proxy]
    bbox_cache = UsdGeom.BBoxCache(time_code, purposes, useExtentsHint=True)

    combined_range = Gf.Range3d()
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        if prim.IsValid():
            bbox = bbox_cache.ComputeWorldBound(prim)
            aligned_range = bbox.ComputeAlignedRange()
            combined_range.UnionWith(aligned_range)

    if combined_range.IsEmpty():
        center = Gf.Vec3d(0, 0, 0)
    else:
        center = combined_range.GetMidpoint()
    return center


def get_pivot(stage: Usd.Stage, path: str) -> hou.Matrix4:
    """Return the transform for the pivot of a primitive."""

    prim = stage.GetPrimAtPath(path)
    if prim.IsValid():
        xformable = UsdGeom.Xformable(prim)
        time_code = Usd.TimeCode(hou.frame())
        world_matrix = xformable.ComputeLocalToWorldTransform(time_code)
        pivot = hou.Matrix4(world_matrix)  # ty: ignore[invalid-argument-type]
    else:
        pivot = hou.Matrix4(1)

    return pivot


def get_unique_prim_path(
    stage: Usd.Stage, path: str, existing_paths: Sequence[str]
) -> str:
    """Return an available path under parent_path by appending an incrementing
    integer.
    """

    prim = stage.GetPrimAtPath(path)
    if not prim.IsValid():
        return path

    parent_path = prim.GetPath().GetParentPath()
    base_name = re.sub(r'_\d+$', '', prim.GetName())

    for i in range(1, MAX_ITERATIONS):
        child_path = parent_path.AppendChild(f'{base_name}_{i}')
        if child_path.pathString in existing_paths:
            continue
        if not stage.GetPrimAtPath(child_path):
            return child_path.pathString
    raise RuntimeError(f'Exceeded maximum limit of {MAX_ITERATIONS} iterations.')


def get_xform_from_parms(parms: dict[str, Any]) -> hou.Matrix4:
    """Return a transform matrix built from parameter values."""

    transform_dict = {
        'translate': (parms['tx'], parms['ty'], parms['tz']),
        'rotate': (parms['rx'], parms['ry'], parms['rz']),
        'scale': (parms['sx'], parms['sy'], parms['sz']),
        'pivot': (parms['px'], parms['py'], parms['pz']),
        'pivot_rotate': (parms['pivot_rx'], parms['pivot_ry'], parms['pivot_rz']),
    }
    xform = hou.hmath.buildTransform(transform_dict)

    comp_dict = {
        'translate': (
            parms['pivot_comp_tx'],
            parms['pivot_comp_ty'],
            parms['pivot_comp_tz'],
        ),
        'rotate': (
            parms['pivot_comp_rx'],
            parms['pivot_comp_ry'],
            parms['pivot_comp_rz'],
        ),
        'scale': (
            parms['pivot_comp_sx'],
            parms['pivot_comp_sy'],
            parms['pivot_comp_sz'],
        ),
    }
    comp_xform = hou.hmath.buildTransform(comp_dict)
    return xform * comp_xform


def get_lop_network(node: hou.LopNode) -> hou.LopNetwork | None:
    """Return the LopNetwork by walking up the node hierarchy."""

    current = node
    while current is not None:
        if isinstance(current, hou.LopNetwork):
            return current
        current = current.parent()
    return None


def is_identity(xform: hou.Matrix4) -> bool:
    """Return whether the matrix is identity within tolerance."""

    delta = xform - hou.Matrix4(1)
    return not any(abs(value) > TOLERANCE for value in delta.asTuple())


def createViewerStateTemplate() -> hou.ViewerStateTemplate:
    """Create and return the ViewerStateTemplate to register."""

    template = hou.ViewerStateTemplate(STATE_NAME, STATE_LABEL, STATE_CATEGORY)
    template.bindFactory(State)
    template.bindIcon(NODE_TYPE.icon())

    State.bind_handles(template)
    State.bind_hotkeys(template)
    State.bind_menus(template)

    return template
