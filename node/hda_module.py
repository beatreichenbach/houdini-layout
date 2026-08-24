import re
from typing import TypeVar

import hou
from hutil.Qt import QtCore, QtWidgets


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
        find = self._from_field.value()
        replace = self._to_field.value()

        node = hou.pwd()
        multi_parm = node.parm('primitives')
        count = multi_parm.evalAsInt()
        offset = multi_parm.multiParmStartOffset()

        for i in range(count):
            index = offset + i

            if destination:
                parm = node.parm(f'destinationprim{index}')
                value = self._find_and_replace(parm.eval(), find, replace, regex)
                parm.set(value)
            if source:
                parm = node.parm(f'sourceprim{index}')
                value = self._find_and_replace(parm.eval(), find, replace, regex)
                parm.set(value)

    @staticmethod
    def _find_and_replace(text: str, find: str, replace: str, regex: bool) -> str:
        """Return a replaced text."""

        if regex:
            return re.sub(find, replace, text)
        else:
            return text.replace(find, replace)


def open_dialog():
    """Open the Find and Replace Dialog."""

    dialog = FindReplaceDialog()
    dialog.setParent(hou.qt.mainWindow(), QtCore.Qt.Tool)
    dialog.exec_()


def remove_missing() -> None:
    """Remove missing primitives."""

    node = hou.pwd()
    multi_parm = node.parm('primitives')
    count = multi_parm.evalAsInt()
    offset = multi_parm.multiParmStartOffset()

    stage = node.stage()

    for i in reversed(range(count)):
        index = offset + i

        path = node.evalParm(f'destinationprim{index}')
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid():
            multi_parm.removeMultiParmInstance(i)
            continue

        path = node.evalParm(f'sourceprim{index}')
        if not path:
            continue

        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid():
            multi_parm.removeMultiParmInstance(i)
            continue


T = TypeVar('T', bound=hou.paneTabType)


def get_pane_tab(pane_tab_type: type[T]) -> T | None:
    """Return the active SceneViewer."""

    pane_tab = hou.ui.paneTabUnderCursor()
    if pane_tab is not None and pane_tab.type() == pane_tab_type:
        return pane_tab

    pane_tabs = hou.ui.currentPaneTabs()
    for pane_tab in pane_tabs:
        if pane_tab.type() == pane_tab_type:
            return pane_tab
    return None


def get_layout_node(pwd: hou.Node, node_type_name: str) -> hou.Node | None:
    """Return a Layout node. Create it if needed."""

    selection = hou.selectedNodes()

    if selection:
        current_node = selection[0]
    else:
        current_node = pwd.displayNode()

    if current_node is not None:
        if current_node.type().name() == node_type_name:
            return current_node
        else:
            node = current_node.createOutputNode(node_type_name)
            return node
    else:
        if pwd.childTypeCategory() == hou.lopNodeTypeCategory():
            node = pwd.createNode(node_type_name)
            node.moveToGoodPosition()
            return node
    return None


def enter_state(node_type_name: str, mode: str) -> None:
    """Enter the edit state of the Layout node. Create a node if needed."""

    scene_viewer = get_pane_tab(hou.paneTabType.SceneViewer)

    if scene_viewer is None:
        return

    node_type = hou.nodeType(hou.lopNodeTypeCategory(), node_type_name)
    viewer_state = node_type.definition().sections()['DefaultState'].contents()
    if scene_viewer.currentState() not in ('lopview', viewer_state):
        if mode == 'translate':
            scene_viewer.enterTranslateToolState()
        elif mode == 'rotate':
            scene_viewer.enterRotateToolState()
        elif mode == 'scale':
            scene_viewer.enterScaleToolState()
        return

    network_editor = get_pane_tab(hou.paneTabType.NetworkEditor)
    if not network_editor:
        return

    pwd = network_editor.pwd()
    node = get_layout_node(pwd, node_type_name)
    if node is None:
        return

    node.setCachedUserData('mode', mode)
    node.setDisplayFlag(True)
    node.setSelected(True, clear_all_selected=True)

    scene_viewer.setCurrentState(viewer_state)
    scene_viewer.enterCurrentNodeState()
