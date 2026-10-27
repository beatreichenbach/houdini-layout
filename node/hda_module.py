import re
from typing import cast

import hou
from hutil.Qt import QtCore, QtWidgets  # ty: ignore[unresolved-import]


class FindReplaceDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent=parent)

        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle('Find and Replace')
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setSizeGripEnabled(False)

        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        # Options
        checkbox_layout = QtWidgets.QHBoxLayout()
        layout.addLayout(checkbox_layout)

        self._destination_check = QtWidgets.QCheckBox('Destination')
        self._destination_check.setChecked(True)
        checkbox_layout.addWidget(self._destination_check)

        self._source_check = QtWidgets.QCheckBox('Source')
        self._source_check.setChecked(True)
        checkbox_layout.addWidget(self._source_check)

        checkbox_layout.addStretch(1)

        self._regex_check = QtWidgets.QCheckBox('Regex')
        checkbox_layout.addWidget(self._regex_check)

        # Fields
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self._from_field = hou.qt.InputField(hou.qt.InputField.StringType, 1)
        form.addRow('From', self._from_field)

        self._to_field = hou.qt.InputField(hou.qt.InputField.StringType, 1)
        form.addRow('To', self._to_field)

        # Buttons
        buttons = QtWidgets.QDialogButtonBox()
        replace_button = buttons.addButton(
            'Replace', QtWidgets.QDialogButtonBox.AcceptRole
        )
        buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        replace_button.setDefault(True)
        layout.addWidget(buttons)

    def accept(self) -> None:
        self._find_and_replace_primitives()
        super().accept()

    def _find_and_replace_primitives(self) -> None:
        """Find and replace text in primitive paths on the node."""

        destination = self._destination_check.isChecked()
        source = self._source_check.isChecked()
        regex = self._regex_check.isChecked()
        find = str(self._from_field.value())
        replace = str(self._to_field.value())

        node = cast(hou.LopNode, hou.pwd())
        multi_parm = node.parm('primitives')
        if multi_parm is None:
            return
        count = multi_parm.evalAsInt()
        offset = multi_parm.multiParmStartOffset()

        for i in range(count):
            index = offset + i

            if destination:
                parm = cast(hou.Parm, node.parm(f'destinationprim{index}'))
                path = parm.evalAsString()
                value = self._find_and_replace(path, find, replace, regex)
                parm.set(value)
            if source:
                parm = cast(hou.Parm, node.parm(f'sourceprim{index}'))
                path = parm.evalAsString()
                value = self._find_and_replace(path, find, replace, regex)
                parm.set(value)

    @staticmethod
    def _find_and_replace(text: str, find: str, replace: str, regex: bool) -> str:
        """Return a replaced text."""

        if regex:
            return re.sub(find, replace, text)
        else:
            return text.replace(find, replace)


def find_and_replace() -> None:
    """Open the Find and Replace Dialog."""

    dialog = FindReplaceDialog()
    dialog.setParent(hou.qt.mainWindow(), QtCore.Qt.Tool)
    dialog.exec_()


def remove_missing() -> None:
    """Remove missing primitives."""

    node = cast(hou.LopNode, hou.pwd())
    stage = node.stage()
    if stage is None:
        return

    multi_parm = cast(hou.Parm, node.parm('primitives'))
    count = multi_parm.evalAsInt()
    offset = multi_parm.multiParmStartOffset()
    for i in reversed(range(count)):
        index = offset + i

        path = cast(str, node.evalParm(f'destinationprim{index}'))
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid():
            multi_parm.removeMultiParmInstance(i)
            continue

        path = cast(str, node.evalParm(f'sourceprim{index}'))
        if not path:
            continue

        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid():
            multi_parm.removeMultiParmInstance(i)
            continue


def remove_duplicates() -> None:
    """Remove duplicate primitives."""

    node = cast(hou.LopNode, hou.pwd())
    stage = node.stage()
    if stage is None:
        return

    multi_parm = cast(hou.Parm, node.parm('primitives'))
    count = multi_parm.evalAsInt()
    offset = multi_parm.multiParmStartOffset()

    paths = []
    duplicate_indices = []
    for i in range(count):
        index = offset + i

        path = node.evalParm(f'destinationprim{index}')
        if path in paths:
            duplicate_indices.append(i)
        else:
            paths.append(path)

    for i in reversed(duplicate_indices):
        multi_parm.removeMultiParmInstance(i)


def get_pane_tab(pane_tab_type: hou.EnumValue) -> hou.PaneTab | None:
    """Return the active SceneViewer."""

    pane_tab = hou.ui.paneTabUnderCursor()
    if pane_tab is not None and pane_tab.type() == pane_tab_type:
        return pane_tab

    pane_tabs = hou.ui.currentPaneTabs()
    for pane_tab in pane_tabs:
        if pane_tab.type() == pane_tab_type:
            return pane_tab
    return None


def get_layout_node(
    pwd: hou.LopNode | hou.LopNetwork, node_type_name: str
) -> hou.LopNode | None:
    """Return a Layout node. Create it if needed."""

    selection = hou.selectedNodes()

    current_node: hou.Node | None
    if selection and isinstance(selection[0], hou.LopNode):
        current_node = selection[0]
    else:
        current_node = pwd.displayNode()

    if current_node is not None:
        if not isinstance(current_node, hou.LopNode):
            return None
        if current_node.type().name() == node_type_name:
            return current_node
        return current_node.createOutputNode(node_type_name)

    if pwd.childTypeCategory() == hou.lopNodeTypeCategory():
        node = cast(hou.LopNode, pwd.createNode(node_type_name))
        node.moveToGoodPosition()
        return node
    return None


def enter_state(node_type: hou.OpNodeType, mode: str) -> None:
    """Enter the edit state of the Layout node. Create a node if needed."""

    definition = node_type.definition()
    if definition is None:
        return

    pane_tab = get_pane_tab(hou.paneTabType.SceneViewer)
    if not isinstance(pane_tab, hou.SceneViewer):
        return

    scene_viewer = pane_tab
    viewer_state = definition.sections()['DefaultState'].contents()
    if scene_viewer.currentState() not in ('lopview', 'scenegraphselect', viewer_state):
        if mode == 'translate':
            scene_viewer.enterTranslateToolState()
        elif mode == 'rotate':
            scene_viewer.enterRotateToolState()
        elif mode == 'scale':
            scene_viewer.enterScaleToolState()
        return

    pane_tab = get_pane_tab(hou.paneTabType.NetworkEditor)
    if pane_tab is None:
        return

    if not isinstance(pane_tab, hou.NetworkEditor):
        return

    pwd = pane_tab.pwd()
    if not isinstance(pwd, (hou.LopNetwork, hou.LopNode)):
        return

    node = get_layout_node(pwd, node_type.name())
    if node is None:
        return

    node.setCachedUserData('mode', mode)
    node.setDisplayFlag(True)
    node.setSelected(True, clear_all_selected=True)

    scene_viewer.setCurrentState(viewer_state)  # ty: ignore[invalid-argument-type]
    scene_viewer.enterCurrentNodeState()
