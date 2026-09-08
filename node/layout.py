import re

import hou
from pxr import Gf, Sdf, Usd, UsdGeom


def create_primitive(
    stage: Usd.Stage, destination_path: str, source_path: str
) -> Usd.Prim:
    """Create the primitive."""

    prim = stage.DefinePrim(destination_path)
    prim.GetReferences().AddReference(
        stage.GetRootLayer().identifier,
        Sdf.Path(source_path),
    )
    return prim


def set_transform(
    prim: Usd.Prim,
    translate: tuple[float, float, float],
    rotate: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> None:
    """Set the transform on a primitive."""

    rotation: Gf.Rotation = (
        Gf.Rotation(Gf.Vec3d.XAxis(), rotate[0])
        * Gf.Rotation(Gf.Vec3d.YAxis(), rotate[1])
        * Gf.Rotation(Gf.Vec3d.ZAxis(), rotate[2])
    )
    mx = (
        Gf.Matrix4d().SetScale(scale)
        * Gf.Matrix4d().SetRotate(rotation)
        * Gf.Matrix4d().SetTranslate(translate)
    )

    xformable = UsdGeom.Xformable(prim)
    suffix = get_unique_xform_op_suffix(xformable, 'layout')
    op = xformable.AddTransformOp(opSuffix=suffix)
    op.Set(mx)
    xformable.SetXformOpOrder([op])


def get_unique_xform_op_suffix(xformable: UsdGeom.Xformable, suffix: str) -> str:
    """Return a unique XFormOp suffix."""

    suffixes = tuple(
        op.GetOpName().split(':')[-1] for op in xformable.GetOrderedXformOps()
    )
    if suffix not in suffixes:
        return suffix

    if match := re.search(r'(.+?)(\d+)$', suffix):
        base = match.group(1)
        number = int(match.group(2))
    else:
        base = suffix
        number = 1

    unique_suffix = suffix
    while unique_suffix in suffixes:
        unique_suffix = f'{base}{number}'
        number += 1

    return unique_suffix


def layout() -> None:
    """Create duplicate primitives from the layout node."""

    node = hou.pwd()
    parent = node.parent()
    stage: Usd.Stage = node.editableStage()

    multi_parm = parent.parm('primitives')
    count = multi_parm.evalAsInt()
    offset = multi_parm.multiParmStartOffset()

    for i in range(count):
        index = offset + i
        destination_path = parent.evalParm(f'destinationprim{index}')
        source_path = parent.evalParm(f'sourceprim{index}')

        if not destination_path:
            continue

        if source_path:
            source_prim = stage.GetPrimAtPath(source_path)
            if not source_prim.IsValid():
                node.addWarning(f'Source primitive invalid: {source_path}.')
                continue

        prim = stage.GetPrimAtPath(destination_path)
        if not prim.IsValid():
            if not source_path:
                continue
            prim = create_primitive(stage, destination_path, source_path)

        # Transforms
        translate = parent.evalParmTuple(f't{index}')
        rotate = parent.evalParmTuple(f'r{index}')
        scale = parent.evalParmTuple(f's{index}')

        set_transform(prim, translate, rotate, scale)


layout()
