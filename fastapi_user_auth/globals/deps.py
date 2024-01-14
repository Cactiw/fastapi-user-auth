from typing import Annotated, Optional

from fastapi import Depends

from fastapi_user_auth import globals as g
from fastapi_user_auth.auth.exceptions import AuthError, ErrorCode

from fastapi_amis_admin.utils.translation import i18n as _

# 获取当前登录的用户
get_user_or_none = g.auth.get_current_user

CurrentUserOrNone: Optional[g.UserModel] = Annotated[Optional[g.UserModel], Depends(get_user_or_none)]


def get_user_or_error(user: CurrentUserOrNone):
    """获取当前登录用户,如果未登录则抛出异常"""
    if not user:
        raise AuthError(status=ErrorCode.USER_IS_NOT_LOGIN, msg=_("User not logged in"), status_code=401)
    return user


CurrentUser: g.UserModel = Annotated[g.UserModel, Depends(get_user_or_error)]
