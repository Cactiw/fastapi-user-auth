from typing import Any, Dict, List, Union

from casbin import Enforcer
from fastapi_amis_admin import amis
from fastapi_amis_admin.admin import FormAdmin, ModelAction, PageSchemaAdmin
from fastapi_amis_admin.amis import SchemaNode
from fastapi_amis_admin.amis.components import ActionType, FormItem, Page
from fastapi_amis_admin.amis.constants import LevelEnum
from fastapi_amis_admin.crud.schema import BaseApiOut
from fastapi_amis_admin.models import Field
from pydantic import BaseModel
from pydantic.fields import ModelField
from starlette.requests import Request

from fastapi_user_auth.auth.crud import (
    casbin_get_permissions_by_role_id,
    casbin_get_permissions_by_user_id,
)
from fastapi_user_auth.auth.models import Role, User
from fastapi_user_auth.mixins.admin import AuthModelAdmin
from fastapi_user_auth.utils import (
    casbin_permission_encode,
    casbin_update_subject_permissions,
    casbin_update_subject_roles,
    get_admin_action_options_by_subject,
)


def get_admin_permission_fields_rows(
    admin: PageSchemaAdmin,
    action: str,
) -> List[Dict[str, Any]]:
    """获取指定页面权限的字段权限,用于amis组件"""
    rows = []
    fields = {}
    if isinstance(admin, AuthModelAdmin):  # 模型管理
        if action == "list":  # 列表展示模型
            fields = admin.list_permission_fields
        elif action == "create":  # 创建模型
            fields = admin.create_permission_fields
        elif action == "update":  # 更新模型
            fields = admin.update_permission_fields
        elif action == "read":  # 详情模型
            fields = admin.read_permission_fields
        else:
            pass
        for name, label in fields.items():
            rows.append(
                {
                    "label": label,
                    "rol": f"page:{action}:{name}",
                }
            )
        # 列表筛选模型
        if action == "list":
            fields = admin.filter_permission_fields
            for name, label in fields.items():
                rows.append(
                    {
                        "label": label,
                        "rol": f"page:filter:{name}",
                    }
                )

    elif isinstance(admin, FormAdmin):  # todo 表单管理
        pass
    return rows


class CasbinBaseSubAction(ModelAction):
    def __init__(self, admin, **kwargs):
        super().__init__(admin, **kwargs)
        if self.admin.model.__table__.name == Role.__tablename__:
            self._subject = "r"
        elif self.admin.model.__table__.name == User.__tablename__:
            self._subject = "u"
        else:
            raise Exception("暂不支持的主体模型")

    async def get_subject_by_id(self, item_id: str) -> str:
        # 从数据库获取用户选择的数据列表
        items = await self.admin.fetch_items(item_id)
        if self._subject == "r":  # 角色管理
            return "r:" + items[0].key
        elif self._subject == "u":  # 用户管理
            return "u:" + items[0].username
        else:  # 其他管理
            return ""


class CasbinUpdateSubRolesAction(CasbinBaseSubAction):
    """主体Casbin角色基类"""

    _implicit: bool = True
    form_init = True

    action = ActionType.Dialog(
        name="update_subject_roles",
        icon="fa fa-check",
        tooltip="设置角色",
        dialog=amis.Dialog(),
        level=LevelEnum.warning,
    )

    class schema(BaseModel):
        role_keys: str = Field(
            None,
            title="角色列表",
            amis_form_item=amis.Transfer(
                selectMode="table",
                resultListModeFollowSelect=True,
                columns=[
                    {"name": "key", "label": "角色标识"},
                    {"name": "name", "label": "角色名称"},
                    {"name": "desc", "label": "角色描述"},
                    {"name": "role_names", "label": "子角色"},
                ],
                source="",
                valueField="key",
            ),
        )

    async def get_form_item(self, request: Request, modelfield: ModelField) -> Union[FormItem, SchemaNode]:
        item = await super().get_form_item(request, modelfield)
        from fastapi_user_auth.admin import RoleAdmin  # 防止循环导入

        role_admin = self.admin.app.get_admin_or_create(RoleAdmin)
        if item.name == "role_keys":  # 为角色树形选择器数据指定API源
            # value
            item.source = f"post:{role_admin.router_path}/list?page=1&perPage=100"
        return item

    async def get_init_data(self, request: Request, **kwargs) -> BaseApiOut[Any]:
        # 从数据库获取角色的权限列表
        item_id = request.query_params.get("item_id")
        if not item_id:
            return BaseApiOut(data=self.schema())
        subject = await self.get_subject_by_id(item_id)
        if not subject:
            return BaseApiOut(status=0, msg="暂不支持的模型")
        role_keys = await self.site.auth.enforcer.get_implicit_roles_for_user(subject)
        return BaseApiOut(data=self.schema(role_keys=",".join(role_keys).replace("r:", "")))

    async def handle(self, request: Request, item_id: List[str], data: BaseModel, **kwargs):
        """更新角色Casbin权限"""
        subject = await self.get_subject_by_id(item_id[0])
        if not subject:
            return BaseApiOut(status=0, msg="暂不支持的模型")
        # 更新角色列表
        enforcer: Enforcer = self.site.auth.enforcer
        await casbin_update_subject_roles(enforcer, subject, data.role_keys)  # 更新角色权限
        # 返回动作处理结果
        return BaseApiOut(data="success")


class CasbinBaseSubPermAction(CasbinBaseSubAction):
    """主体Casbin权限基类"""

    _implicit: bool = True
    form_init = False
    # 配置动作基本信息
    # action = ActionType.Drawer(
    #     name="view_subject_permissions",
    #     icon="fa fa-check",
    #     tooltip="查看权限",
    #     drawer=amis.Drawer(),
    #     level=LevelEnum.warning
    # )

    action = ActionType.Dialog(
        name="view_subject_permissions",
        icon="fa fa-check",
        tooltip="查看权限",
        dialog=amis.Dialog(),
        level=LevelEnum.warning,
    )

    # 创建动作表单数据模型
    class schema(BaseModel):
        permissions: str = Field(
            None,
            title="权限列表",
            amis_form_item=amis.InputTree(
                multiple=True,
                source="",
                searchable=True,
                showOutline=True,
                cascade=True,
                autoCheckChildren=False,
                heightAuto=True,
            ),
            # amis_form_item=amis.Transfer(
            #     selectMode="tree", # chained
            #     searchResultMode="tree",
            #     sortable=True,
            #     source="",
            #     searchable=True,
            #     resultListModeFollowSelect=True,
            #     resultSearchable=True,
            # ),
        )

    async def get_form_item(self, request: Request, modelfield: ModelField) -> Union[FormItem, SchemaNode]:
        item = await super().get_form_item(request, modelfield)
        if item.name == "permissions":  # 为角色树形选择器数据指定API源
            item.source = f"{self.site.settings.site_path}/auth/site_admin_actions_options"
        return item


class CasbinViewSubPermAction(CasbinBaseSubPermAction):
    """查看主体Casbin权限,暂时不支持单个主体的权限可视化配置"""

    _implicit: bool = True
    form_init = True

    action = ActionType.Dialog(
        name="view_subject_permissions",
        icon="fa fa-check",
        tooltip="查看权限",
        dialog=amis.Dialog(actions=[]),
        level=LevelEnum.warning,
    )

    async def get_form_item(self, request: Request, modelfield: ModelField) -> Union[FormItem, SchemaNode]:
        item = await super().get_form_item(request, modelfield)
        if item.name == "permissions":  # 为角色树形选择器数据指定API源
            item.multiple = True
        return item

    async def get_init_data(self, request: Request, **kwargs) -> BaseApiOut[Any]:
        # 从数据库获取角色的权限列表
        item_id = request.query_params.get("item_id")
        if not item_id:
            return BaseApiOut(data=self.schema())
        if self._subject == "r":  # 角色管理
            permissions = await casbin_get_permissions_by_role_id(self.site.auth, item_id, implicit=self._implicit)
        elif self._subject == "u":  # 用户管理
            permissions = await casbin_get_permissions_by_user_id(self.site.auth, item_id, implicit=self._implicit)
        else:  # 其他管理
            permissions = []
        return BaseApiOut(data=self.schema(permissions=",".join(permissions)))

    async def handle(self, request: Request, item_id: List[str], data: BaseModel, **kwargs):
        return BaseApiOut(status=1, msg="请通过的【设置权限】更新设置!")


class CasbinUpdateSubFieldPermAction(CasbinBaseSubPermAction):
    """更新主体Casbin权限,字段权限"""

    _implicit: bool = True

    action = ActionType.Dialog(
        name="update_subject_field_permissions",
        icon="fa fa-gavel",
        tooltip="设置字段权限",
        dialog=amis.Dialog(actions=[]),
        level=LevelEnum.warning,
    )

    async def get_form_item(self, request: Request, modelfield: ModelField) -> Union[FormItem, SchemaNode]:
        item = await super().get_form_item(request, modelfield)
        if item.name == "permissions":  # 为角色树形选择器数据指定API源
            item.multiple = False
            item.source = f"{self.router_path}/get_admin_action_options?item_id=$id"  # 获取对方权限列表
        return item

    def register_router(self):
        super().register_router()

        # 获取全部页面权限
        @self.router.get("/get_admin_action_options", response_model=BaseApiOut)
        async def _get_admin_action_options(request: Request, item_id: str):
            # 获取对方权限列表
            subject = await self.get_subject_by_id(item_id)
            options = await get_admin_action_options_by_subject(self.site.auth.enforcer, subject, self.site)
            return BaseApiOut(data=options)

        return self

    async def get_page(self, request: Request) -> Page:
        page = await super().get_page(request)
        form = await self.get_form(request)

        tree: amis.InputTree = form.body[0]
        # https://aisuda.bce.baidu.com/amis/zh-CN/docs/concepts/event-action#%E5%8A%A8%E4%BD%9C%E9%97%B4%E6%95%B0%E6%8D%AE%E4
        # %BC%A0%E9%80%92
        tree = tree.update_from_kwargs(
            onEvent={
                "change": {
                    "actions": [
                        {
                            "actionType": "reload",
                            "componentId": "u:matrix-form",
                            "args": {"permission": "$value", "item_id": "$id"},
                        }
                    ]
                }
            },
        )
        page.body = [
            {
                "type": "grid",
                "columns": [
                    tree,
                    amis.Form(
                        title="字段权限",
                        debug=True,
                        name="matrix-form",
                        id="u:matrix-form",
                        api=amis.AmisAPI(
                            method="POST", url=f"{self.router_path}{self.form_path}" + "?item_id=${IF(ids, ids, id)}"
                        ),
                        body=[
                            {
                                "type": "matrix-checkboxes",
                                "name": "matrix",
                                "label": "字段权限配置",
                                "rowLabel": "字段名称",
                                "multiple": False,
                                "singleSelectMode": "row",
                                "yCheckAll": True,
                                "source": f"{self.site.settings.site_path}/auth/admin_action_fields_options?"
                                f"permission=$permission&item_id=$item_id&subject={self._subject}",
                                # "id": "u:matrix-fields"
                            }
                        ],
                    ),
                ],
            }
        ]
        return page

    async def handle(self, request: Request, item_id: List[str], data: BaseModel, **kwargs):
        print("handle", item_id, data)
        return BaseApiOut(data="success")


class CasbinUpdateSubPermsAction(CasbinViewSubPermAction):
    """更新主体Casbin权限"""

    _implicit: bool = False
    action = ActionType.Dialog(
        name="update_subject_permissions",
        icon="fa fa-gavel",
        tooltip="设置页面权限",
        dialog=amis.Dialog(),
        level=LevelEnum.warning,
    )

    async def handle(self, request: Request, item_id: List[str], data: BaseModel, **kwargs):
        """更新角色Casbin权限"""
        subject = await self.get_subject_by_id(item_id[0])
        if not subject:
            return BaseApiOut(status=0, msg="暂不支持的模型")
        # 权限列表 #todo 可能添加了不存在的权限,或者超过了显示的权限范围
        permissions = [rule for rule in data.permissions.split(",") if rule]  # 分割权限列表,去除空值
        site_rule = casbin_permission_encode(self.site.unique_id, "admin:page")
        if permissions and site_rule not in permissions:  # 添加后台站点默认权限
            permissions.append(site_rule)
        enforcer: Enforcer = self.site.auth.enforcer
        await casbin_update_subject_permissions(enforcer, subject, permissions)  # 更新角色权限
        return BaseApiOut(data="success")