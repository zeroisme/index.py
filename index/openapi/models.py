import os
import typing
import asyncio
from abc import abstractmethod, ABCMeta

from aiofiles import open as asyncopen
from starlette.datastructures import UploadFile
from index.config import config

from .utils import currying, merge_mapping


__all__ = [
    "Model",
    "IntField",
    "FloatField",
    "StrField",
    "BooleanField",
    "FileField",
    "ModelField",
    "ListField",
]

EMPTY_VALUE = ("", {}, [], None, ())

ALLOWS_TYPE = ("array", "boolean", "integer", "number", "object", "string")


class VerifyError(Exception):
    pass


class FieldVerifyError(VerifyError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ModelFieldVerifyError(FieldVerifyError):
    def __init__(self, message: typing.Dict[str, typing.Any]) -> None:
        super().__init__(message)
        self.message = message


class Field(metaclass=ABCMeta):
    def __init__(
        self,
        *,
        description: str = "No description.",
        example: typing.Any = None,
        allow_null: bool = False,
        default: typing.Any = None,
    ):
        self.description = description
        self.example = example
        self.allow_null = allow_null
        self.default = default

    def check_null(self, value: typing.Any) -> typing.Any:
        if value is None:
            if self.allow_null:
                return self.default
            raise FieldVerifyError("Not allowed to be empty.")
        return value

    @abstractmethod
    async def verify(self, value: typing.Any) -> typing.Any:
        pass

    def openapi(self) -> typing.Dict[str, typing.Any]:
        schema = {
            "description": self.description,
        }
        if self.default not in EMPTY_VALUE:
            schema["default"] = self.default
        return schema


class ChoiceField(Field, metaclass=ABCMeta):
    def __init__(self, *, choices: typing.Iterable[str] = (), **kwargs):
        super().__init__(**kwargs)
        self.choices = choices
        if choices:
            assert self.default in choices, "default value must be in choices"

    def check_choice(self, value: typing.Any) -> typing.Any:
        if self.choices and value in self.choices:
            return value
        raise FieldVerifyError(f"value must be in {self.choices}")

    @abstractmethod
    async def verify(self, value: typing.Any) -> typing.Any:
        pass

    def openapi(self) -> typing.Dict[str, typing.Any]:
        schema = {"description": self.description, "": list(self.choices)}
        if self.default not in EMPTY_VALUE:
            schema["default"] = self.default
        return schema


class FileField(Field):
    def __init__(
        self,
        prefix: str,
        *,
        description: str = "No description.",
        allow_null: bool = False,
        save: typing.Callable[[UploadFile], typing.Awaitable[str]] = None,
    ):
        super().__init__(description=description, allow_null=allow_null)
        self.prefix = prefix
        assert not prefix.startswith("/"), 'Prefix cannot start with "/".'
        if save is not None:
            setattr(self, "save", save)

    async def save(self, file: UploadFile) -> str:
        abspath = os.path.join(config.path, self.prefix, file.filename)
        if os.path.exists(abspath):
            raise FileExistsError(abspath)
        async with asyncopen(abspath, "w+") as target:
            while True:
                data = await file.read(1024)
                if not data:
                    break
                await target.write(data)
        return abspath

    async def verify(self, value: UploadFile) -> str:
        """return file path/url"""
        try:
            return await self.save(value)
        except FileExistsError:
            raise FieldVerifyError(f'This file "{value.filename}" already exists.')

    def openapi(self) -> typing.Dict[str, typing.Any]:
        schema = {"description": self.description, "type": "string", "format": "binary"}
        return schema


class BooleanField(Field):
    def __init__(self, *, default: typing.Any = None, **kwargs):
        assert default in (True, False), "default in BooleanField must be bool"
        kwargs["default"] = default
        super().__init__(**kwargs)

    def verify(self, value: typing.Any) -> bool:
        value = self.check_null(value)
        return bool(value)

    def openapi(self) -> typing.Dict[str, typing.Any]:
        schema = super().openapi()
        schema.update(
            {"type": "boolean",}
        )
        return schema


class IntField(ChoiceField):
    def verify(self, value: typing.Any) -> int:
        value = self.check_null(value)
        value = self.check_choice(value)
        try:
            return int(value)
        except ValueError:
            raise FieldVerifyError("Must be an integer.")

    def openapi(self) -> typing.Dict[str, typing.Any]:
        schema = super().openapi()
        schema.update(
            {"type": "integer",}
        )
        return schema


class FloatField(ChoiceField):
    def verify(self, value: typing.Any) -> float:
        value = self.check_null(value)
        value = self.check_choice(value)
        try:
            return float(value)
        except ValueError:
            raise FieldVerifyError("Must be an floating point number.")

    def openapi(self) -> typing.Dict[str, typing.Any]:
        schema = super().openapi()
        schema.update(
            {"type": "number",}
        )
        return schema


class StrField(ChoiceField):
    def verify(self, value: typing.Any) -> str:
        value = self.check_null(value)
        value = self.check_choice(value)
        return str(value)

    def openapi(self) -> typing.Dict[str, typing.Any]:
        schema = super().openapi()
        schema.update(
            {"type": "string",}
        )
        return schema


class MatchFieldMeta(type):
    def __new__(
        cls,
        name: str,
        bases: typing.Iterable[typing.Any],
        namespace: typing.Dict[str, typing.Any],
    ):
        fields = {}
        for key, value in namespace:
            if isinstance(value, Field):
                fields[key] = value
                if isinstance(value, FileField):
                    cls._content_type = "multipart/form-data"
                if isinstance(value, (ListField, ModelField)):
                    cls._content_type = "application/json"
        cls.fields = fields


class Model(metaclass=MatchFieldMeta):
    default_content_type = "application/json"
    _content_type: str
    fields: typing.Dict[str, Field]

    def __init__(
        self,
        raw_data: typing.Dict[str, typing.Any],
        *,
        default: typing.Dict[str, typing.Any] = None,
        description: str = "",
    ) -> None:
        self.raw_data = raw_data
        self.description = description
        if default:
            self.data = merge_mapping(raw_data, default)
        else:
            self.data = raw_data

    async def verify(self) -> typing.Dict[str, str]:
        errors = {}
        for name, field in self.fields:
            try:
                data = field.verify(self.data.get(name))
                if asyncio.iscoroutine(data):
                    data = await data
                setattr(self, name, data)
            except FieldVerifyError as e:
                errors[name] = e.message
        return errors

    def openapi(self) -> typing.Dict[str, typing.Any]:
        required = []
        properties = []
        for name, field in self.fields:
            if not field.allow_null:
                required.append(name)
            properties.append({name: field.openapi()})
        return {"type": "object", "required": required, "properties": properties}

    @property
    @classmethod
    def content_type(cls) -> str:
        if hasattr(cls, "_content_type"):
            return cls._content_type
        return cls.default_content_type


class ModelField(Field):
    def __init__(
        self, model: Model, *, default: typing.Dict[str, typing.Any] = {}, **kwargs
    ):
        kwargs["default"] = default
        super().__init__(**kwargs)
        self.model = currying(model)(default=default)

    async def verify(self, value: typing.Any) -> typing.Any:
        model = self.model(value)()
        errors = await model.verify()
        if errors:
            raise ModelFieldVerifyError(errors)
        return model

    def openapi(self) -> typing.Dict[str, typing.Any]:
        schema = super().openapi()
        schema.update(self.model.openapi())
        return schema


class ListField(Field):
    def __init__(
        self, field: Field, *, default: typing.List[typing.Any] = [], **kwargs
    ):
        kwargs["default"] = default
        super().__init__(**kwargs)
        self.field = field

    async def verify(self, value: typing.Iterable) -> typing.List[typing.Any]:
        result = []
        for each in value:
            data = self.field.verify(each)
            if asyncio.iscoroutine(data):
                data = await data
            result.append(data)
        return result

    def openapi(self) -> typing.Dict[str, typing.Any]:
        schema = super().openapi()
        schema.update({"type": "array", "items": self.field.openapi()})
        return schema
