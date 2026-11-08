"""极简 Tekla Open API / pythonnet 仿真层（仅用于在无 Windows 环境跑单元测试）。

它只实现 tekla_server.py 建模工具用到的公开 API 子集：
Point、Identifier、Part/Beam/PolyBeam/ContourPlate、Position、Component、
ComponentInput、ModelObjectSelector、Model。行为约定与 pythonnet 一致：
out 参数返回元组、GUID 用 Identifier.GUID、Insert 返回 bool。
"""

import sys
import types

MODEL = None
COMPONENTS = []


class _Enum:
    def __init__(self, **values):
        self.__dict__.update(values)


class Point:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X, self.Y, self.Z = float(x), float(y), float(z)

    def __repr__(self):
        return f"Point({self.X},{self.Y},{self.Z})"


class Identifier:
    _next = 1

    def __init__(self, guid=None):
        if guid is None:
            self.GUID = f"00000000-0000-0000-0000-{Identifier._next:012d}"
            Identifier._next += 1
        else:
            self.GUID = guid
        self.ID = Identifier._next


class Position:
    PlaneEnum = _Enum(MIDDLE="MIDDLE", LEFT="LEFT", RIGHT="RIGHT")
    RotationEnum = _Enum(FRONT="FRONT", TOP="TOP", BACK="BACK", BOTTOM="BOTTOM")
    DepthEnum = _Enum(MIDDLE="MIDDLE", FRONT="FRONT", BEHIND="BEHIND")

    def __init__(self):
        self.Plane = Position.PlaneEnum.MIDDLE
        self.Rotation = Position.RotationEnum.FRONT
        self.Depth = Position.DepthEnum.MIDDLE
        self.PlaneOffset = 0.0
        self.RotationOffset = 0.0
        self.DepthOffset = 0.0


class Chamfer:
    def __init__(self, *args):
        self.types = args


class ContourPoint:
    def __init__(self, point, chamfer=None):
        self.Point = point
        self.Chamfer = chamfer


class Contour:
    def __init__(self):
        self.Points = []

    def AddContourPoint(self, contour_point):
        self.Points.append(contour_point)
        return True


class ModelObject:
    ModelObjectEnum = _Enum(ASSEMBLY="ASSEMBLY", BOLT_ARRAY="BOLT_ARRAY",
                            WELD="WELD", REBARGROUP="REBARGROUP",
                            REFERENCE_MODEL="REFERENCE_MODEL")


class Part(ModelObject):
    def __init__(self):
        self.Identifier = Identifier()
        self.Profile = types.SimpleNamespace(ProfileString="")
        self.Material = types.SimpleNamespace(MaterialString="")
        self.Position = Position()
        self.Class = ""
        self.Name = ""
        self.Finish = ""
        self.Deleted = False

    # 报表属性：只实现 BOM / 快照用到的两个
    def GetReportProperty(self, name, placeholder):
        mapping = {"PROFILE": self.Profile.ProfileString,
                   "MATERIAL": self.Material.MaterialString}
        if name in mapping:
            return (True, mapping[name])
        return (False, placeholder)

    def GetPhase(self):
        return (False, None)

    def GetComponents(self):
        return Enumerator([])

    def Insert(self):
        MODEL.add(self)
        return True

    def Modify(self):
        return True

    def Delete(self):
        self.Deleted = True
        MODEL.remove(self)
        return True


class Beam(Part):
    BeamTypeEnum = _Enum(BEAM="BEAM", COLUMN="COLUMN", PANEL="PANEL",
                         PAD_FOOTING="PAD_FOOTING", STRIP_FOOTING="STRIP_FOOTING")

    def __init__(self, beam_type=None):
        super().__init__()
        self.Type = beam_type if beam_type is not None else Beam.BeamTypeEnum.BEAM
        self.StartPoint = Point()
        self.EndPoint = Point()


class PolyBeam(Part):
    def __init__(self):
        super().__init__()
        self.Contour = Contour()

    def AddContourPoint(self, contour_point):
        self.Contour.AddContourPoint(contour_point)


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X, self.Y, self.Z = float(x), float(y), float(z)

    def __repr__(self):
        return f"Vector({self.X},{self.Y},{self.Z})"


class SpiralBeam(Part):
    """螺旋梁（Tekla 2026 实测）：StartPoint/EndPoint 只读，几何由旋转轴+转角+升高定义。"""

    def __init__(self):
        super().__init__()
        self._start = Point()
        self._end = Point()
        self.RotationAxisBasePoint = None
        self.RotationAxisDirection = None
        self.RotationAxisUpPoint = None
        self.RotationAngle = 0.0
        self.TotalRise = 0.0
        self.TwistAngleStart = 0.0
        self.TwistAngleEnd = 0.0

    @property
    def StartPoint(self):
        return self._start

    @StartPoint.setter
    def StartPoint(self, value):
        raise TypeError("property is read-only")

    @property
    def EndPoint(self):
        return self._end

    @EndPoint.setter
    def EndPoint(self, value):
        raise TypeError("property is read-only")


class ContourPlate(Part):
    def __init__(self):
        super().__init__()
        self.Contour = Contour()

    def AddContourPoint(self, contour_point):
        self.Contour.AddContourPoint(contour_point)


class ComponentInput:
    def __init__(self):
        self.objects = []
        self.points = []

    def AddInputObject(self, obj):
        self.objects.append(obj)
        return True

    def AddInputObjects(self, objects):
        self.objects.extend(list(objects))
        return True

    def AddOneInputPosition(self, point):
        self.points.append(point)
        return True

    def AddTwoInputPositions(self, first, second):
        self.points.extend([first, second])
        return True


class Component:
    def __init__(self):
        self.Identifier = Identifier()
        self.Name = ""
        self.Number = 0
        self.attributes = {}
        self.attributes_file = ""
        self.input = None

    def LoadAttributesFromFile(self, name):
        self.attributes_file = name
        return True

    def SetAttribute(self, key, value):
        self.attributes[key] = value
        return True

    def SetComponentInput(self, component_input):
        self.input = component_input
        return True

    def Insert(self):
        if self.input is None or not self.input.objects:
            return False
        COMPONENTS.append(self)
        self.Identifier = Identifier()
        return True


class AutoDirectionTypeEnum:
    AUTODIR_FROM_ATTRIBUTE_FILE = "AUTODIR_FROM_ATTRIBUTE_FILE"
    AUTODIR_GLOBAL = "AUTODIR_GLOBAL"


class Connection(Component):
    def __init__(self):
        super().__init__()
        self.primary = None
        self.secondaries = []

    def SetPrimaryObject(self, obj):
        self.primary = obj
        return True

    def SetSecondaryObject(self, obj):
        self.secondaries.append(obj)
        return True

    def SetSecondaryObjects(self, objects):
        self.secondaries.extend(list(objects))
        return True

    def Insert(self):
        if self.primary is None:
            return False
        COMPONENTS.append(self)
        self.Identifier = Identifier()
        return True


class Detail(Component):
    def __init__(self):
        super().__init__()
        self.primary = None
        self.reference_point = None

    def SetPrimaryObject(self, obj):
        self.primary = obj
        return True

    def SetReferencePoint(self, point):
        self.reference_point = point
        return True

    def Insert(self):
        if self.primary is None:
            return False
        COMPONENTS.append(self)
        self.Identifier = Identifier()
        return True


class Enumerator:
    def __init__(self, items):
        self._items = list(items)
        self._index = -1

    def MoveNext(self):
        self._index += 1
        return self._index < len(self._items)

    @property
    def Current(self):
        return self._items[self._index]


class ModelObjectSelector:
    def __init__(self):
        self.selected = []

    def GetSelectedObjects(self):
        return Enumerator(self.selected)

    def GetAllObjects(self):
        return Enumerator(list(MODEL.objects.values()))

    def GetAllObjectsWithType(self, model_object_enum):
        return Enumerator([])

    def Select(self, objects):
        self.selected = list(objects)
        return True


class Model:
    """单例：Tekla 只有一个运行中的模型，工具每次 new Model() 都指向同一实例。"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "objects"):
            self.objects = {}
            self.commit_count = 0

    def add(self, model_object):
        self.objects[model_object.Identifier.GUID] = model_object

    def remove(self, model_object):
        self.objects.pop(model_object.Identifier.GUID, None)

    def GetConnectionStatus(self):
        return True

    def CommitChanges(self):
        self.commit_count += 1
        return True

    def GetIdentifierByGUID(self, guid):
        obj = self.objects.get(str(guid))
        return obj.Identifier if obj is not None else None

    def SelectModelObject(self, identifier):
        for obj in self.objects.values():
            if obj.Identifier.GUID == identifier.GUID:
                return obj
        return None

    def GetModelObjectSelector(self):
        return ModelObjectSelector()

    def GetPhases(self):
        return []

    def GetInfo(self):
        return types.SimpleNamespace(ModelName="fake", ModelPath="/tmp/fake")


def _module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def install():
    """把仿真模块注册进 sys.modules，必须在 import tekla_server 之前调用。"""
    global MODEL
    MODEL = Model()

    clr = _module("clr", AddReference=lambda *a, **k: None)
    clr.AddReference = lambda *a, **k: None

    class _Guid(str):
        pass

    class _ArrayList(list):
        def Add(self, item):
            self.append(item)

    system = _module(
        "System",
        String=str,
        Double=float,
        Int32=int,
        Guid=_Guid,
        Collections=_module("System.Collections", ArrayList=_ArrayList),
    )

    tekla = _module("Tekla")
    structures = _module("Tekla.Structures")
    tekla.Structures = structures
    model = _module("Tekla.Structures.Model",
                    Model=Model, ModelObject=ModelObject, Part=Part, Beam=Beam,
                    PolyBeam=PolyBeam, ContourPlate=ContourPlate,
                    SpiralBeam=SpiralBeam,
                    Contour=Contour, ContourPoint=ContourPoint,
                    Chamfer=Chamfer, Position=Position,
                    Component=Component, Connection=Connection, Detail=Detail,
                    AutoDirectionTypeEnum=AutoDirectionTypeEnum,
                    ComponentInput=ComponentInput,
                    ModelObjectSelector=ModelObjectSelector,
                    Identifier=Identifier)
    geo = _module("Tekla.Structures.Geometry3d", Point=Point, Vector=Vector)
    ui = _module("Tekla.Structures.Model.UI",
                 ModelObjectSelector=ModelObjectSelector)
    structures.Model = model
    structures.Geometry3d = geo
    model.UI = ui

    class FastMCP:
        def __init__(self, name="fake", version=None):
            self.name = name
            self.version = version
            self.tools = {}

        def tool(self):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn
            return decorator

        def run(self, transport=None):
            return None

    mcp = _module("mcp")
    mcp_server = _module("mcp.server")
    fastmcp = _module("mcp.server.fastmcp", FastMCP=FastMCP)
    mcpserver = _module("mcp.server.mcpserver", MCPServer=FastMCP)
    mcp.server = mcp_server
    mcp_server.fastmcp = fastmcp
    mcp_server.mcpserver = mcpserver


def reset():
    """清空伪模型，供每个用例独立运行。"""
    MODEL.objects.clear()
    MODEL.commit_count = 0
    del COMPONENTS[:]
