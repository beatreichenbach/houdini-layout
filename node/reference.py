from typing import cast

import hou
from pxr import Sdf, Usd, UsdShade


def set_variant(prim: Usd.Prim, variant_set: str, variant_name: str) -> None:
    """Set the variant for a primitive."""

    vset = prim.GetVariantSet(variant_set)
    vset.SetVariantSelection(variant_name)


def create_references() -> None:
    """Create references for the duplicated primitives."""

    node = cast(hou.LopNode, hou.pwd())
    parent = cast(hou.LopNode, node.parent())
    stage = node.editableStage()
    if stage is None:
        return

    root_layer_id = stage.GetRootLayer().identifier
    created_references = []

    primitives_parm = cast(hou.Parm, parent.parm('primitives'))
    primitives_count = primitives_parm.evalAsInt()
    primitives_offset = primitives_parm.multiParmStartOffset()
    for i in range(primitives_count):
        index = primitives_offset + i
        destination_path = cast(str, parent.evalParm(f'destinationprim{index}'))
        source_path = cast(str, parent.evalParm(f'sourceprim{index}'))

        if not destination_path:
            continue

        destination_prim = stage.GetPrimAtPath(destination_path)
        if not destination_prim.IsValid():
            if not source_path:
                continue

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

        # Variants
        variants_parm = cast(hou.Parm, parent.parm(f'variants{index}'))
        variants_count = variants_parm.evalAsInt()
        variants_offset = variants_parm.multiParmStartOffset()
        for j in range(variants_count):
            index2 = variants_offset + j
            variant_set_name = cast(str, parent.evalParm(f'variantset{index}_{index2}'))
            variant_name = cast(str, parent.evalParm(f'variantname{index}_{index2}'))

            variant_set = destination_prim.GetVariantSet(variant_set_name)
            variant_set.SetVariantSelection(variant_name)


create_references()
