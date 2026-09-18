from typing import cast

import hou
from pxr import Sdf, UsdShade


def create_references() -> None:
    """Create duplicate primitives from the layout node."""

    node = cast(hou.LopNode, hou.pwd())
    parent = cast(hou.LopNode, node.parent())
    stage = node.editableStage()
    if stage is None:
        return

    root_layer_id = stage.GetRootLayer().identifier
    created_references = []

    multi_parm = cast(hou.Parm, parent.parm('primitives'))
    count = multi_parm.evalAsInt()
    offset = multi_parm.multiParmStartOffset()
    for i in range(count):
        index = offset + i
        destination_path = cast(str, parent.evalParm(f'destinationprim{index}'))
        source_path = cast(str, parent.evalParm(f'sourceprim{index}'))

        if not destination_path:
            continue

        destination_prim = stage.GetPrimAtPath(destination_path)
        if destination_prim.IsValid():
            continue

        if source_path:
            source_prim = stage.GetPrimAtPath(source_path)
            if not source_prim.IsValid():
                node.addWarning(f'Invalid source primitive: {source_path}.')
                continue

            if destination_path in created_references:
                continue

            # Create reference
            destination_prim = stage.DefinePrim(destination_path)
            references = destination_prim.GetReferences()
            references.AddReference(root_layer_id, Sdf.Path(source_path))
            created_references.append(destination_path)

            # Bind Materials
            binding_api = UsdShade.MaterialBindingAPI(source_prim)
            bound_material, _relationship = binding_api.ComputeBoundMaterial()
            if bound_material:
                dest_binding_api = UsdShade.MaterialBindingAPI.Apply(destination_prim)
                dest_binding_api.Bind(bound_material)


create_references()
