import functools
import json
import os
import pathlib
import sys
import tempfile
import typing
from datetime import datetime, timedelta
from enum import Enum

import click
import mock
import pytest
import yaml
from click.testing import CliRunner

from flytekit import FlyteContextManager
from flytekit.clis.sdk_in_container import pyflyte
from flytekit.clis.sdk_in_container.constants import CTX_CONFIG_FILE
from flytekit.clis.sdk_in_container.helpers import FLYTE_REMOTE_INSTANCE_KEY
from flytekit.clis.sdk_in_container.run import (
    REMOTE_FLAG_KEY,
    RUN_LEVEL_PARAMS_KEY,
    DateTimeType,
    DurationParamType,
    FileParamType,
    FlyteLiteralConverter,
    JsonParamType,
    get_entities_in_file,
    run_command,
)
from flytekit.configuration import Config, Image, ImageConfig
from flytekit.core.task import task
from flytekit.core.type_engine import TypeEngine
from flytekit.image_spec.image_spec import ImageBuildEngine, ImageSpecBuilder
from flytekit.models.types import SimpleType
from flytekit.remote import FlyteRemote

WORKFLOW_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "workflow.py")
REMOTE_WORKFLOW_FILE = "https://raw.githubusercontent.com/flyteorg/flytesnacks/8337b64b33df046b2f6e4cba03c74b7bdc0c4fb1/cookbook/core/flyte_basics/basic_workflow.py"
IMPERATIVE_WORKFLOW_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "imperative_wf.py")
DIR_NAME = os.path.dirname(os.path.realpath(__file__))


@pytest.fixture
def remote():
    with mock.patch("flytekit.clients.friendly.SynchronousFlyteClient") as mock_client:
        flyte_remote = FlyteRemote(config=Config.auto(), default_project="p1", default_domain="d1")
        flyte_remote._client = mock_client
        return flyte_remote


def test_pyflyte_run_wf(remote):
    with mock.patch("flytekit.clis.sdk_in_container.helpers.get_and_save_remote_with_click_context"):
        runner = CliRunner()
        module_path = WORKFLOW_FILE
        result = runner.invoke(pyflyte.main, ["run", module_path, "my_wf", "--help"], catch_exceptions=False)

        assert result.exit_code == 0


def test_imperative_wf():
    runner = CliRunner()
    result = runner.invoke(
        pyflyte.main,
        ["run", IMPERATIVE_WORKFLOW_FILE, "wf", "--in1", "hello", "--in2", "world"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0


def test_copy_all_files():
    runner = CliRunner()
    result = runner.invoke(
        pyflyte.main,
        ["run", "--copy-all", IMPERATIVE_WORKFLOW_FILE, "wf", "--in1", "hello", "--in2", "world"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0


def test_remote_files():
    runner = CliRunner()
    result = runner.invoke(
        pyflyte.main,
        ["run", REMOTE_WORKFLOW_FILE, "my_wf", "--a", "1", "--b", "Hello"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0


def test_pyflyte_run_cli():
    runner = CliRunner()
    parquet_file = os.path.join(DIR_NAME, "testdata/df.parquet")
    result = runner.invoke(
        pyflyte.main,
        [
            "run",
            WORKFLOW_FILE,
            "my_wf",
            "--a",
            "1",
            "--b",
            "Hello",
            "--c",
            "1.1",
            "--d",
            '{"i":1,"a":["h","e"]}',
            "--e",
            "[1,2,3]",
            "--f",
            '{"x":1.0, "y":2.0}',
            "--g",
            parquet_file,
            "--i",
            "2020-05-01",
            "--j",
            "20H",
            "--k",
            "RED",
            "--l",
            '{"hello": "world"}',
            "--remote",
            os.path.join(DIR_NAME, "testdata"),
            "--image",
            os.path.join(DIR_NAME, "testdata"),
            "--h",
            "--n",
            json.dumps([{"x": parquet_file}]),
            "--o",
            json.dumps({"x": [parquet_file]}),
            "--p",
            "Any",
        ],
        catch_exceptions=False,
    )
    print(result.stdout)
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "input",
    ["1", os.path.join(DIR_NAME, "testdata/df.parquet"), '{"x":1.0, "y":2.0}', "2020-05-01", "RED"],
)
def test_union_type1(input):
    runner = CliRunner()
    result = runner.invoke(
        pyflyte.main,
        [
            "run",
            os.path.join(DIR_NAME, "workflow.py"),
            "test_union1",
            "--a",
            input,
        ],
        catch_exceptions=False,
    )
    print(result.stdout)
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "input",
    [2.0, '{"i":1,"a":["h","e"]}', "[1, 2, 3]"],
)
def test_union_type2(input):
    runner = CliRunner()
    env = '{"foo": "bar"}'
    result = runner.invoke(
        pyflyte.main,
        ["run", "--overwrite-cache", "--envs", env, os.path.join(DIR_NAME, "workflow.py"), "test_union2", "--a", input],
        catch_exceptions=False,
    )
    print(result.stdout)
    assert result.exit_code == 0


def test_union_type_with_invalid_input():
    runner = CliRunner()
    result = runner.invoke(
        pyflyte.main,
        [
            "--verbose",
            "run",
            os.path.join(DIR_NAME, "workflow.py"),
            "test_union2",
            "--a",
            "hello",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 2


def test_get_entities_in_file():
    e = get_entities_in_file(WORKFLOW_FILE, False)
    assert e.workflows == ["my_wf"]
    assert e.tasks == ["get_subset_df", "print_all", "show_sd", "test_union1", "test_union2"]
    assert e.all() == ["my_wf", "get_subset_df", "print_all", "show_sd", "test_union1", "test_union2"]


@pytest.mark.parametrize(
    "working_dir, wf_path",
    [
        (pathlib.Path("test_nested_wf"), os.path.join("a", "b", "c", "d", "wf.py")),
        (pathlib.Path("test_nested_wf", "a"), os.path.join("b", "c", "d", "wf.py")),
        (pathlib.Path("test_nested_wf", "a", "b"), os.path.join("c", "d", "wf.py")),
        (pathlib.Path("test_nested_wf", "a", "b", "c"), os.path.join("d", "wf.py")),
        (pathlib.Path("test_nested_wf", "a", "b", "c", "d"), os.path.join("wf.py")),
    ],
)
def test_nested_workflow(working_dir, wf_path, monkeypatch: pytest.MonkeyPatch):
    runner = CliRunner()
    base_path = os.path.dirname(os.path.realpath(__file__))
    # Change working directory without side-effects (i.e. just for this test)
    monkeypatch.chdir(os.path.join(base_path, working_dir))
    result = runner.invoke(
        pyflyte.main,
        [
            "run",
            wf_path,
            "wf_id",
            "--m",
            "wow",
        ],
        catch_exceptions=False,
    )
    assert result.stdout.strip() == "wow"
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "wf_path",
    [("collection_wf.py"), ("map_wf.py"), ("dataclass_wf.py")],
)
def test_list_default_arguments(wf_path):
    runner = CliRunner()
    dir_name = os.path.dirname(os.path.realpath(__file__))
    result = runner.invoke(
        pyflyte.main,
        [
            "run",
            os.path.join(dir_name, "default_arguments", wf_path),
            "wf",
        ],
        catch_exceptions=False,
    )
    print(result.stdout)
    assert result.exit_code == 0


# default case, what comes from click if no image is specified, the click param is configured to use the default.
ic_result_1 = ImageConfig(
    default_image=Image(name="default", fqn="ghcr.io/flyteorg/mydefault", tag="py3.9-latest"),
    images=[Image(name="default", fqn="ghcr.io/flyteorg/mydefault", tag="py3.9-latest")],
)
# test that command line args are merged with the file
ic_result_2 = ImageConfig(
    default_image=Image(name="default", fqn="cr.flyte.org/flyteorg/flytekit", tag="py3.9-latest"),
    images=[
        Image(name="default", fqn="cr.flyte.org/flyteorg/flytekit", tag="py3.9-latest"),
        Image(name="asdf", fqn="ghcr.io/asdf/asdf", tag="latest"),
        Image(name="xyz", fqn="docker.io/xyz", tag="latest"),
        Image(name="abc", fqn="docker.io/abc", tag=None),
    ],
)
# test that command line args override the file
ic_result_3 = ImageConfig(
    default_image=Image(name="default", fqn="cr.flyte.org/flyteorg/flytekit", tag="py3.9-latest"),
    images=[
        Image(name="default", fqn="cr.flyte.org/flyteorg/flytekit", tag="py3.9-latest"),
        Image(name="xyz", fqn="ghcr.io/asdf/asdf", tag="latest"),
        Image(name="abc", fqn="docker.io/abc", tag=None),
    ],
)

ic_result_4 = ImageConfig(
    default_image=Image(name="default", fqn="flytekit", tag="EYuIM3pFiH1kv8pM85SuxA.."),
    images=[
        Image(name="default", fqn="flytekit", tag="EYuIM3pFiH1kv8pM85SuxA.."),
        Image(name="xyz", fqn="docker.io/xyz", tag="latest"),
        Image(name="abc", fqn="docker.io/abc", tag=None),
    ],
)

IMAGE_SPEC = os.path.join(os.path.dirname(os.path.realpath(__file__)), "imageSpec.yaml")


@mock.patch("flytekit.configuration.default_images.DefaultImages.default_image")
@pytest.mark.parametrize(
    "image_string, leaf_configuration_file_name, final_image_config",
    [
        ("ghcr.io/flyteorg/mydefault:py3.9-latest", "no_images.yaml", ic_result_1),
        ("asdf=ghcr.io/asdf/asdf:latest", "sample.yaml", ic_result_2),
        ("xyz=ghcr.io/asdf/asdf:latest", "sample.yaml", ic_result_3),
        (IMAGE_SPEC, "sample.yaml", ic_result_4),
    ],
)
@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "darwin",
    reason="Github macos-latest image does not have docker installed as per https://github.com/orgs/community/discussions/25777",
)
def test_pyflyte_run_run(mock_image, image_string, leaf_configuration_file_name, final_image_config):
    mock_image.return_value = "cr.flyte.org/flyteorg/flytekit:py3.9-latest"

    class TestImageSpecBuilder(ImageSpecBuilder):
        def build_image(self, img):
            ...

    ImageBuildEngine.register("test", TestImageSpecBuilder())

    @task
    def a():
        ...

    mock_click_ctx = mock.MagicMock()
    mock_remote = mock.MagicMock()
    image_tuple = (image_string,)
    image_config = ImageConfig.validate_image(None, "", image_tuple)

    run_level_params = {
        "project": "p",
        "domain": "d",
        "image_config": image_config,
    }

    pp = pathlib.Path.joinpath(
        pathlib.Path(__file__).parent.parent.parent, "configuration/configs/", leaf_configuration_file_name
    )

    obj = {
        RUN_LEVEL_PARAMS_KEY: run_level_params,
        REMOTE_FLAG_KEY: True,
        FLYTE_REMOTE_INSTANCE_KEY: mock_remote,
        CTX_CONFIG_FILE: str(pp),
    }
    mock_click_ctx.obj = obj

    def check_image(*args, **kwargs):
        assert kwargs["image_config"] == final_image_config

    mock_remote.register_script.side_effect = check_image

    run_command(mock_click_ctx, a)()


def test_file_param():
    m = mock.MagicMock()
    l = FileParamType().convert(__file__, m, m)
    assert l.local
    r = FileParamType().convert("https://tmp/file", m, m)
    assert r.local is False


class Color(Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


@pytest.mark.parametrize(
    "python_type, python_value",
    [
        (typing.Union[typing.List[int], str, Color], "flyte"),
        (typing.Union[typing.List[int], str, Color], "red"),
        (typing.Union[typing.List[int], str, Color], [1, 2, 3]),
        (typing.List[int], [1, 2, 3]),
        (typing.Dict[str, int], {"flyte": 2}),
    ],
)
def test_literal_converter(python_type, python_value):
    get_upload_url_fn = functools.partial(
        FlyteRemote(Config.auto()).client.get_upload_signed_url, project="p", domain="d"
    )
    click_ctx = click.Context(click.Command("test_command"), obj={"remote": True})
    ctx = FlyteContextManager.current_context()
    lt = TypeEngine.to_literal_type(python_type)

    lc = FlyteLiteralConverter(
        click_ctx, ctx, literal_type=lt, python_type=python_type, get_upload_url_fn=get_upload_url_fn
    )

    assert lc.convert(click_ctx, ctx, python_value) == TypeEngine.to_literal(ctx, python_value, python_type, lt)


def test_enum_converter():
    pt = typing.Union[str, Color]

    get_upload_url_fn = functools.partial(FlyteRemote(Config.auto()).client.get_upload_signed_url)
    click_ctx = click.Context(click.Command("test_command"), obj={"remote": True})
    ctx = FlyteContextManager.current_context()
    lt = TypeEngine.to_literal_type(pt)
    lc = FlyteLiteralConverter(click_ctx, ctx, literal_type=lt, python_type=pt, get_upload_url_fn=get_upload_url_fn)
    union_lt = lc.convert(click_ctx, ctx, "red").scalar.union

    assert union_lt.stored_type.simple == SimpleType.STRING
    assert union_lt.stored_type.enum_type is None

    pt = typing.Union[Color, str]
    lt = TypeEngine.to_literal_type(typing.Union[Color, str])
    lc = FlyteLiteralConverter(click_ctx, ctx, literal_type=lt, python_type=pt, get_upload_url_fn=get_upload_url_fn)
    union_lt = lc.convert(click_ctx, ctx, "red").scalar.union

    assert union_lt.stored_type.simple is None
    assert union_lt.stored_type.enum_type.values == ["red", "green", "blue"]


def test_duration_type():
    t = DurationParamType()
    assert t.convert(value="1 day", param=None, ctx=None) == timedelta(days=1)

    with pytest.raises(click.BadParameter):
        t.convert(None, None, None)


def test_datetime_type():
    t = DateTimeType()

    assert t.convert("2020-01-01", None, None) == datetime(2020, 1, 1)

    now = datetime.now()
    v = t.convert("now", None, None)
    assert v.day == now.day
    assert v.month == now.month


def test_json_type():
    t = JsonParamType()
    assert t.convert(value='{"a": "b"}', param=None, ctx=None) == {"a": "b"}

    with pytest.raises(click.BadParameter):
        t.convert(None, None, None)

    # test that it loads a json file
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        json.dump({"a": "b"}, f)
        f.flush()
        assert t.convert(value=f.name, param=None, ctx=None) == {"a": "b"}

    # test that if the file is not a valid json, it raises an error
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("asdf")
        f.flush()
        with pytest.raises(click.BadParameter):
            t.convert(value=f.name, param="asdf", ctx=None)

    # test if the file does not exist
    with pytest.raises(click.BadParameter):
        t.convert(value="asdf", param=None, ctx=None)

    # test if the file is yaml and ends with .yaml it works correctly
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump({"a": "b"}, f)
        f.flush()
        assert t.convert(value=f.name, param=None, ctx=None) == {"a": "b"}
