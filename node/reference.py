import hou
from pxr import Sdf, Usd


def create_references() -> None:
    """Create duplicate primitives from the layout node."""

    node: hou.LopNode = hou.pwd()
    parent = node.parent()
    stage = node.editableStage()
    if stage is None:
        return

    root_layer_id = stage.GetRootLayer().identifier

    multi_parm = parent.parm('primitives')
    count = multi_parm.evalAsInt()
    offset = multi_parm.multiParmStartOffset()
    for i in range(count):
        index = offset + i
        destination_path = parent.evalParm(f'destinationprim{index}')
        source_path = parent.evalParm(f'sourceprim{index}')

        if not destination_path:
            continue

        destination_prim = stage.GetPrimAtPath(destination_path)
        if destination_prim.IsValid():
            continue

        if source_path:
            source_prim = stage.GetPrimAtPath(source_path)
            if not source_prim.IsValid():
                node.addWarning(f'Source primitive is invalid: {source_path}.')
                continue

            # Create reference
            destination_prim = stage.DefinePrim(destination_path)
            references = destination_prim.GetReferences()
            references.AddReference(root_layer_id, Sdf.Path(source_path))


create_references()
