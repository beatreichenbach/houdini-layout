import re

import hou
from pxr import Gf, UsdGeom

INSTANCE_PATTERN = re.compile(r'(.+?)(\d+)$')
X_AXIS = Gf.Vec3d.XAxis()
Y_AXIS = Gf.Vec3d.YAxis()
Z_AXIS = Gf.Vec3d.ZAxis()


def set_transform(
    xformable: UsdGeom.Xformable,
    translate: tuple[float, float, float],
    rotate: tuple[float, float, float],
    scale: tuple[float, float, float],
    suffix: str,
) -> None:
    """Set the transform on a primitive."""

    rotation = (
        Gf.Rotation(X_AXIS, rotate[0])
        * Gf.Rotation(Y_AXIS, rotate[1])
        * Gf.Rotation(Z_AXIS, rotate[2])
    )
    xform = (
        Gf.Matrix4d().SetScale(scale)
        * Gf.Matrix4d().SetRotate(rotation)
        * Gf.Matrix4d().SetTranslate(translate)
    )
    op = get_xform_op_by_suffix(xformable, suffix)
    if op is not None:
        # Set the attribute to override the reference in case the op exists.
        order_attr = xformable.GetXformOpOrderAttr()
        current_order = list(order_attr.Get() or [])
        order_attr.Set(current_order)
    else:
        op = xformable.AddTransformOp(opSuffix=suffix)
    op.Set(xform)


def get_xform_op_by_suffix(
    xformable: UsdGeom.Xformable, suffix: str
) -> UsdGeom.XformOp | None:
    """Return an XformOp on a prim by its suffix."""

    for op in xformable.GetOrderedXformOps():
        if op.GetOpName().rsplit(':', 1)[-1] == suffix:
            return op
    return None


def get_unique_xform_op_suffix(xformable: UsdGeom.Xformable, suffix: str) -> str:
    """Return a unique XFormOp suffix."""

    suffixes = tuple(
        op.GetOpName().rsplit(':', 1)[-1] for op in xformable.GetOrderedXformOps()
    )
    if suffix not in suffixes:
        return suffix

    if match := INSTANCE_PATTERN.search(suffix):
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


def transform_primitives() -> None:
    """Create duplicate primitives from the layout node."""

    node: hou.LopNode = hou.pwd()
    parent = node.parent()
    stage = node.editableStage()
    if stage is None:
        return

    suffixes: dict[str, str] = {}
    transformed_primitives = []

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
        if not destination_prim.IsValid():
            node.addWarning(f'Invalid primitive: {destination_path}.')
            continue

        if destination_path in transformed_primitives:
            node.addWarning(
                f'Ignoring duplicate entries for primitive: {destination_path}.'
            )
            continue

        # Transforms
        xformable = UsdGeom.Xformable(destination_prim)
        translate = parent.evalParmTuple(f't{index}')
        rotate = parent.evalParmTuple(f'r{index}')
        scale = parent.evalParmTuple(f's{index}')

        suffix = suffixes.get(source_path)
        if suffix is None:
            suffix = get_unique_xform_op_suffix(xformable, 'layout')
            suffixes[destination_path] = suffix

        set_transform(xformable, translate, rotate, scale, suffix)
        transformed_primitives.append(destination_path)


transform_primitives()
