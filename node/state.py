import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import hou
import stateutils as su
import viewerhandle.utils as hu
from pxr import Gf, Usd, UsdGeom


def get_state_name(node_type: hou.OpNodeType) -> str:
    """Return the default state name."""

    sections = node_type.definition().sections()
    state_name = sections['DefaultState'].contents()
    return state_name


NODE_TYPE: hou.OpNodeType = kwargs['type']  # noqa: F821
STATE_NAME = get_state_name(NODE_TYPE)
STATE_LABEL = 'Layout'
STATE_CATEGORY = hou.lopNodeTypeCategory()
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


@dataclass
class TransformParms:
    translate: hou.ParmTuple
    rotate: hou.ParmTuple
    scale: hou.ParmTuple

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

        self.translate.set(hou.Vector3(translate))
        self.rotate.set(hou.Vector3(rotate))
        self.scale.set(hou.Vector3(scale))

    def revert(self) -> None:
        """Revert all parms to defaults."""

        self.translate.revertToDefaults()
        self.rotate.revertToDefaults()
        self.scale.revertToDefaults()


@dataclass
class PrimInstance(TransformParms):
    destinationprim: hou.Parm
    sourceprim: hou.Parm


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
        self.node = None

        self._previous_xform = None

    @staticmethod
    def bind_handles(template: hou.ViewerStateTemplate) -> None:
        """Bind handles to a template."""

        handle_type = 'xform'
        handle_name = 'Xform'
        template.bindHandle(handle_type, handle_name, cache_previous_parms=True)
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

        menu = hou.ViewerStateMenu(STATE_NAME + '_menu', STATE_LABEL)
        menu.addActionItem('duplicate', 'Duplicate', State.duplicate_key)
        menu.addActionItem('remove', 'Remove Edits', State.delete_key)
        template.bindMenu(menu)

    def onEnter(self, kwargs: dict[str, Any]) -> None:
        """Called when the state is entered from an existing node."""

        self._init_node(kwargs['node'])

        if not self._is_valid():
            return

        self.scene_viewer.hudInfo(show=True, template=HUD_TEMPLATE)

    def onExit(self, kwargs: dict[str, Any]) -> None:
        """Called when the state is about to be exited."""

        self.node = None

    def onKeyEvent(self, kwargs) -> bool:
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

        if not self._is_valid():
            return

        menu_item = kwargs.get('menu_item')
        if menu_item == 'duplicate':
            self._duplicate_primitives()

            # Force a Pivot update manually.
            self.xform_handle.update()

        elif menu_item == 'remove':
            self._remove_primitives()

            # Force a Pivot update manually.
            self.xform_handle.update()

    def onBeginHandleToState(self, kwargs: dict[str, Any]) -> None:
        """Called at the start of user interaction with a handle."""

        if not self._is_valid():
            return

        self.scene_viewer.beginStateUndo('Move primitives')

        self._init_transforms()

    def onEndHandleToState(self, kwargs: dict[str, Any]) -> None:
        """Called at the end of user interaction with a handle."""

        if not self._is_valid():
            return

        self._apply_transforms()

        self.scene_viewer.endStateUndo()

    def onHandleToState(self, kwargs: dict[str, Any]) -> None:
        """Called on user interaction with a bound handle."""

        if not self._is_valid():
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

        if not self._is_valid():
            return

        self._update_pivot(kwargs)

    def onSelection(self, kwargs: dict[str, Any]) -> bool:
        """Called when the user selected geometry. Return True to accept the
        selection and stop the selector."""

        if not self._is_valid():
            return True

        self.xform_handle.update()
        return True

    def _is_valid(self) -> bool:
        """Return whether the current state viewer is valid."""

        return self.node is not None

    def _init_node(self, node: hou.OpNode) -> None:
        """Initialize the node."""

        if node.type() != NODE_TYPE:
            return

        self.node = node

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

        network = get_lop_network(self.node)
        stage = self.node.stage()
        if network is None or stage is None:
            return

        selection = get_selection(network, stage)

        paths = ' '.join(selection)
        self.node.parm('primpattern').set(paths)

    def _apply_transforms(self) -> None:
        """Apply the temporary transforms to the multi parm."""

        input_node = self.node.node('IN_transform')
        if input_node is None:
            return

        stage = input_node.stage()
        if stage is None:
            return

        temp = self._get_temp()
        instances = self._get_instances()

        prim_parm = self.node.parm('primpattern')
        pattern = prim_parm.evalAsString()
        delta_xform = temp.get_xform()

        # Revert internal parms and variables
        prim_parm.revertToDefaults()
        temp.revert()
        self._previous_xform = None

        # Apply transform in local space to parameters
        time_code = Usd.TimeCode(hou.frame())
        cache = UsdGeom.XformCache(time_code)
        paths = pattern.split(' ')
        for path in paths:
            instance = instances.get(path)
            if instance is None:
                instance = self._add_primitive(path)

            prim = stage.GetPrimAtPath(path)
            world_matrix_usd = cache.GetLocalToWorldTransform(prim)

            world_matrix = hou.Matrix4(world_matrix_usd)
            local_matrix = world_matrix * delta_xform * world_matrix.inverted()
            previous_xform = instance.get_xform()
            xform = previous_xform * local_matrix
            instance.set_xform(xform, sanitize=True)

    def _move_primitives(self, xform: hou.Matrix4) -> None:
        """Move selected primitives by the xform."""

        # Don't create an undo action if delta is below tolerance.
        delta = xform - hou.Matrix4(1)
        has_value = any(abs(val) > TOLERANCE for val in delta.asTuple())
        if not has_value:
            return

        if self._previous_xform is None:
            self._previous_xform = hou.Matrix4(1)

        new_xform = self._previous_xform * xform
        self._previous_xform = new_xform

        temp = self._get_temp()
        temp.set_xform(new_xform)

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

        multi_parm = self.node.parm('primitives')
        count = multi_parm.evalAsInt()
        offset = multi_parm.multiParmStartOffset()

        self.scene_viewer.beginStateUndo('Remove Edits')

        for i in reversed(range(count)):
            index = offset + i
            destination_path = self.node.evalParm(f'destinationprim{index}')
            if destination_path in selection:
                multi_parm.removeMultiParmInstance(i)

        network.setSelection(())

        self.scene_viewer.endStateUndo()

    def _update_pivot(self, kwargs: dict) -> None:
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

    def _add_primitive(self, path: str, source: str = '') -> PrimInstance:
        """Add a primitive to the multi parm."""

        # Add primitive
        multi_parm = self.node.parm('primitives')

        offset = multi_parm.multiParmStartOffset()
        count = multi_parm.evalAsInt()
        multi_parm.set(count + 1)
        index = offset + count

        # Set parameters
        instance = PrimInstance(
            destinationprim=self.node.parm(f'destinationprim{index}'),
            sourceprim=self.node.parm(f'sourceprim{index}'),
            translate=self.node.parmTuple(f't{index}'),
            rotate=self.node.parmTuple(f'r{index}'),
            scale=self.node.parmTuple(f's{index}'),
        )

        instance.destinationprim.set(path)

        if source:
            instances = self._get_instances()
            source_instance = instances.get(source)

            # NOTE: instances gets cleared
            source_path = self._get_source_path(source, instances)
            instance.sourceprim.set(source_path)

            if source_instance:
                instance.translate.set(source_instance.translate.evalAsFloats())
                instance.rotate.set(source_instance.rotate.evalAsFloats())
                instance.scale.set(source_instance.scale.evalAsFloats())

        return instance

    def _get_temp(self) -> TransformParms:
        """Return the temporary parameters."""

        temp = TransformParms(
            translate=self.node.parmTuple('t'),
            rotate=self.node.parmTuple('r'),
            scale=self.node.parmTuple('s'),
        )
        return temp

    def _get_instances(self) -> dict[str, PrimInstance]:
        """Return a dictionary of all primitive parameters."""

        multi_parm = self.node.parm('primitives')
        count = multi_parm.evalAsInt()
        offset = multi_parm.multiParmStartOffset()

        instances = {}
        for i in range(count):
            index = offset + i

            instance = PrimInstance(
                destinationprim=self.node.parm(f'destinationprim{index}'),
                sourceprim=self.node.parm(f'sourceprim{index}'),
                translate=self.node.parmTuple(f't{index}'),
                rotate=self.node.parmTuple(f'r{index}'),
                scale=self.node.parmTuple(f's{index}'),
            )
            destination_path = instance.destinationprim.evalAsString()
            instances[destination_path] = instance
        return instances

    def _get_source_path(self, path: str, instances: dict[str, PrimInstance]) -> str:
        """
        Return the source prim path of a primitive in the parms.

        Warning: The `instances` parameter is mutable and will be edited.
        """

        if instance := instances.get(path):
            source_path = instance.sourceprim.evalAsString()
            if source_path:
                instances.pop(path, None)
                return self._get_source_path(source_path, instances)
        return path


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
        pivot = hou.Matrix4(world_matrix)
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


def get_xform_from_parms(parms: dict) -> hou.Matrix4:
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


def createViewerStateTemplate() -> hou.ViewerStateTemplate:
    """Create and return the ViewerStateTemplate to register."""

    template = hou.ViewerStateTemplate(STATE_NAME, STATE_LABEL, STATE_CATEGORY)
    template.bindFactory(State)
    template.bindIcon(NODE_TYPE.icon())

    State.bind_handles(template)
    State.bind_hotkeys(template)
    State.bind_menus(template)

    return template
