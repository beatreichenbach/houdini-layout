import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import hou
import stateutils as su
import viewerhandle.utils as hu
from pxr import Gf, Usd, UsdGeom


def get_state_name() -> str:
    """Return the default state name."""

    node_type = kwargs['type']  # noqa: F821
    sections = node_type.definition().sections()
    state_name = sections['DefaultState'].contents()
    return state_name


STATE_NAME = get_state_name()
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

MAX_ITERATIONS = 10000
TOLERANCE = 1e-5


@dataclass
class PrimParms:
    destinationprim: hou.Parm
    sourceprim: hou.Parm
    translate: hou.ParmTuple
    rotate: hou.ParmTuple
    scale: hou.ParmTuple


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

        self.logger = su.Logger(True)
        self.xform_handle = hou.Handle(self.scene_viewer, 'Xform')
        self.node = None

        self._parms = None
        self._selection = None

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

        self.scene_viewer.hudInfo(show=True, template=HUD_TEMPLATE)

        node = kwargs['node']
        if isinstance(node, hou.LopNode):
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

    def onMenuAction(self, kwargs: dict[str, Any]):
        """Called when a context menu choice is selected."""

        if self.node is None:
            return

        menu_item = kwargs.get('menu_item')
        if menu_item == 'duplicate':
            self._duplicate_primitives()

            # NOTE: LopNetwork.setSelection does not update the viewport.
            # Force a Pivot update manually.
            kwargs['selection'] = get_selected_prim_paths(self.node)
            self._update_pivot(kwargs)

        elif menu_item == 'remove':
            selection = get_selected_prim_paths(self.node)
            self._remove_primitives(selection)

            # NOTE: LopNetwork.setSelection does not update the viewport.
            # Force a Pivot update manually.
            kwargs['selection'] = selection
            self._update_pivot(kwargs)

    def onHandleToState(self, kwargs: dict[str, Any]) -> None:
        """Called on user interaction with a bound handle."""

        if self.node is None:
            return

        # NOTE: On hou.uiEventReason.Start the prev_parms are not populated.
        ui_event = kwargs['ui_event']

        if ui_event.reason() == hou.uiEventReason.Active:
            handle = kwargs['handle']
            parms = kwargs['parms']
            previous_parms = kwargs['prev_parms']

            if handle == self.xform_handle.name():
                xform = get_xform(parms)
                previous_xform = get_xform(previous_parms)
                delta_xform = previous_xform.inverted() * xform

                delta = delta_xform - hou.Matrix4(1)
                has_value = any(abs(val) > TOLERANCE for val in delta.asTuple())
                if has_value:
                    self._move_primitives(delta_xform)

    def onStateToHandle(self, kwargs: dict[str, Any]) -> None:
        """Called when node parameters change, to update handle parameters."""

        kwargs['parms'].update(kwargs['state_parms'])

    def onBeginHandleToState(self, kwargs: dict[str, Any]) -> None:
        """Called at the start of user interaction with a handle."""

        self._parms = None

        self._selection = get_selected_prim_paths(self.node)
        parms = self._get_parms()
        for path in self._selection:
            if path not in parms:
                self._init_primitive(path=path)

    def onEndHandleToState(self, kwargs: dict[str, Any]) -> None:
        """Called at the end of user interaction with a handle."""

        self._parms = None
        self._selection = None

    def onSelection(self, kwargs: dict[str, Any]):
        """Called when the user selected geometry. Return True to accept the
        selection and stop the selector."""

        self._update_pivot(kwargs)

    def _move_primitives(self, xform: hou.Matrix4) -> None:
        """Move selected primitives by the xform."""

        if self._selection is None:
            return

        parms = self._get_parms()

        self.scene_viewer.beginStateUndo('Move primitives')

        for path in self._selection:
            prim_parms = parms.get(path)
            if prim_parms is None:
                continue

            transform_dict = {
                'translate': prim_parms.translate.eval(),
                'rotate': prim_parms.rotate.eval(),
                'scale': prim_parms.scale.eval(),
            }
            previous_xform = hou.hmath.buildTransform(transform_dict)

            world_xform = previous_xform * xform

            components = world_xform.explode()
            translate = components['translate']
            rotate = components['rotate']
            scale = components['scale']

            # Sanitize
            rotate = hou.Vector3([v * (abs(v) > TOLERANCE) for v in rotate])

            prim_parms.translate.set(hou.Vector3(translate))
            prim_parms.rotate.set(hou.Vector3(rotate))
            prim_parms.scale.set(hou.Vector3(scale))

        self.scene_viewer.endStateUndo()

    def _duplicate_primitives(self) -> None:
        """Duplicate a primitive."""

        if self.node is None:
            return

        stage = self.node.stage()
        if stage is None:
            return

        primitive_paths = get_selected_prim_paths(self.node)
        duplicated_paths = []

        self.scene_viewer.beginStateUndo('Duplicate primitives')

        for path in primitive_paths:
            target_path = get_unique_prim_path(stage, path, duplicated_paths)
            self._init_primitive(path=target_path, source=path)
            duplicated_paths.append(target_path)

        if lopnet := get_lop_network(self.node):
            lopnet.setSelection(duplicated_paths)

        self.scene_viewer.endStateUndo()

    def _init_primitive(self, path: str, source: str = '') -> None:
        """Initialize a primitive in the layout node."""

        # Get Xform before parameters are set
        xform_path = source if source else path
        xform = self._get_transform(xform_path)

        # Add primitive
        multi_parm = self.node.parm('primitives')

        offset = multi_parm.multiParmStartOffset()
        count = multi_parm.evalAsInt()
        multi_parm.set(count + 1)
        index = offset + count

        # Set parameters
        prim_parms = PrimParms(
            destinationprim=self.node.parm(f'destinationprim{index}'),
            sourceprim=self.node.parm(f'sourceprim{index}'),
            translate=self.node.parmTuple(f't{index}'),
            rotate=self.node.parmTuple(f'r{index}'),
            scale=self.node.parmTuple(f's{index}'),
        )
        parms = self._get_parms()
        parms[path] = prim_parms

        # Set parameters
        prim_parms.destinationprim.set(path)
        if source:
            source_path = self._get_source_prim_path(source)
            prim_parms.sourceprim.set(source_path)

        if xform is not None:
            components = xform.explode()
            translate = components['translate']
            rotate = components['rotate']
            scale = components['scale']

            prim_parms.translate.set(translate)
            prim_parms.rotate.set(rotate)
            prim_parms.scale.set(scale)

    def _remove_primitives(self, selection: Sequence[str]) -> None:
        """Remove the edits for primitives."""

        multi_parm = self.node.parm('primitives')
        count = multi_parm.evalAsInt()
        offset = multi_parm.multiParmStartOffset()

        self.scene_viewer.beginStateUndo('Remove Edits')

        for i in reversed(range(count)):
            index = offset + i

            destination_path = self.node.evalParm(f'destinationprim{index}')
            if destination_path in selection:
                multi_parm.removeMultiParmInstance(i)

        self._parms = None

        stage = self.node.stage()
        paths = [s for s in selection if stage and stage.GetPrimAtPath(s)]
        if lopnet := get_lop_network(self.node):
            lopnet.setSelection(paths)

        self.scene_viewer.endStateUndo()

    def _update_pivot(self, kwargs: dict) -> None:
        """Update the handle's pivot to the current selection."""

        selection = kwargs['selection']

        if not selection:
            self.xform_handle.show(False)
            return

        # Reset State
        kwargs['state_parms'].update(
            {
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
        )

        # Set pivot to selection
        stage = self.node.stage()
        if stage is None:
            return

        center = get_bbox_center(stage=stage, prim_paths=selection)
        kwargs['state_parms']['px'] = center[0]
        kwargs['state_parms']['py'] = center[1]
        kwargs['state_parms']['pz'] = center[2]

        if len(selection) == 1:
            path = selection[0]
            parms = self._get_parms()
            if prim_parms := parms.get(path):
                rotation = prim_parms.rotate.eval()
            else:
                rotation = get_euler_angles(stage, prim_path=path)

            kwargs['state_parms']['pivot_rx'] = rotation[0]
            kwargs['state_parms']['pivot_ry'] = rotation[1]
            kwargs['state_parms']['pivot_rz'] = rotation[2]

        self.xform_handle.update()
        self.xform_handle.show(True)

    def _get_parms(self) -> dict[str, PrimParms]:
        """Return a cached dictionary of PrimParms."""

        if self._parms:
            return self._parms

        multi_parm = self.node.parm('primitives')
        count = multi_parm.evalAsInt()
        offset = multi_parm.multiParmStartOffset()

        self._parms = {}
        for i in range(count):
            index = offset + i

            parms = PrimParms(
                destinationprim=self.node.parm(f'destinationprim{index}'),
                sourceprim=self.node.parm(f'sourceprim{index}'),
                translate=self.node.parmTuple(f't{index}'),
                rotate=self.node.parmTuple(f'r{index}'),
                scale=self.node.parmTuple(f's{index}'),
            )
            destination_path = parms.destinationprim.eval()

            self._parms[destination_path] = parms
        return self._parms

    def _get_transform(self, path: str) -> hou.Matrix4 | None:
        """Return the world space transform of a primitive path."""

        parms = self._get_parms()
        if path in parms:
            prim_parms = parms[path]

            transform_dict = {
                'translate': prim_parms.translate.eval(),
                'rotate': prim_parms.rotate.eval(),
                'scale': prim_parms.scale.eval(),
            }
            xform = hou.hmath.buildTransform(transform_dict)
            return xform

        if stage := self.node.stage():
            prim = stage.GetPrimAtPath(path)
            if prim.IsValid():
                xformable = UsdGeom.Xformable(prim)
                time_code = Usd.TimeCode(hou.frame())
                world_transform = xformable.ComputeLocalToWorldTransform(time_code)
                xform = hou.Matrix4([value for row in world_transform for value in row])
                return xform

        return None

    def _get_source_prim_path(self, path: str) -> str:
        """Return the source prim path of a primitive in the stage."""

        parms = self._get_parms()
        if prim_parms := parms.get(path):
            source_path = prim_parms.sourceprim.eval()
            if source_path:
                return self._get_source_prim_path(source_path)
        return path


def get_bbox_center(stage: Usd.Stage, prim_paths: Sequence[str]) -> Gf.Vec3d:
    """Return the combined bounding box center of a list of primitive paths."""

    time_code = Usd.TimeCode(hou.frame())
    purposes = [UsdGeom.Tokens.default_]
    bbox_cache = UsdGeom.BBoxCache(time_code, purposes)

    combined_range = Gf.Range3d()

    for path in prim_paths:
        prim = stage.GetPrimAtPath(path)
        if prim.IsValid():
            bbox = bbox_cache.ComputeWorldBound(prim)
            aligned_range = bbox.ComputeAlignedRange()
            combined_range.UnionWith(aligned_range)

    if combined_range.IsEmpty():
        return Gf.Vec3d(0, 0, 0)

    return combined_range.GetMidpoint()


def get_euler_angles(stage: Usd.Stage, prim_path: str) -> Gf.Vec3d:
    """Return the rotation in euler angles of a primitive path."""

    time_code = Usd.TimeCode(hou.frame())
    cache = UsdGeom.XformCache(time_code)
    prim = stage.GetPrimAtPath(prim_path)

    if prim.IsValid():
        world_mat = cache.GetLocalToWorldTransform(prim)
        rotation = world_mat.ExtractRotation()
        euler_angles = rotation.Decompose(
            Gf.Vec3d.XAxis(), Gf.Vec3d.YAxis(), Gf.Vec3d.ZAxis()
        )
    else:
        euler_angles = Gf.Vec3d(0, 0, 0)

    return euler_angles


def get_xform(parms: dict) -> hou.Matrix4:
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
    base_name = re.sub(r'_?\d+$', '', prim.GetName())

    for i in range(1, MAX_ITERATIONS):
        child_path = parent_path.AppendChild(f'{base_name}_{i}')
        if child_path.pathString in existing_paths:
            continue
        if not stage.GetPrimAtPath(child_path):
            return child_path.pathString
    raise RuntimeError(f'Exceeded maximum limit of {MAX_ITERATIONS} iterations.')


def get_lop_network(node: hou.LopNode) -> hou.LopNetwork | None:
    """Return the LopNetwork by walking up the node hierarchy."""

    current = node
    while current is not None:
        if isinstance(current, hou.LopNetwork):
            return current
        current = current.parent()
    return None


def get_selected_prim_paths(node: hou.LopNode) -> tuple[str, ...]:
    """Return the paths of the selected primitives in the stage of a node."""

    if node is None:
        return ()

    lop_network = get_lop_network(node)
    if isinstance(lop_network, hou.LopNetwork):
        selection = lop_network.selection()
        return selection
    else:
        return ()


def createViewerStateTemplate() -> hou.ViewerStateTemplate:
    """Create and return the ViewerStateTemplate to register."""

    node_type = kwargs['type']  # noqa: F821

    template = hou.ViewerStateTemplate(STATE_NAME, STATE_LABEL, STATE_CATEGORY)
    template.bindFactory(State)
    template.bindIcon(node_type.icon())

    State.bind_handles(template)
    State.bind_hotkeys(template)
    State.bind_menus(template)

    return template
