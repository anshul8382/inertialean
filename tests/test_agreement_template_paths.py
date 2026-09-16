"""Template file path resolution for agreement DOCX generation."""

import pytest
from flask import Flask

from services.agreement_pdf_paths import template_file_abs_path, template_file_store_path


@pytest.fixture
def flask_ctx(tmp_path):
    tpl_dir = tmp_path / 'static' / 'uploads' / 'templates'
    tpl_dir.mkdir(parents=True)
    sample = tpl_dir / 'sample.docx'
    sample.write_bytes(b'PK')
    app = Flask(__name__)
    app.root_path = str(tmp_path)
    with app.app_context():
        yield str(sample)


@pytest.mark.no_app
def test_template_file_abs_path_web_relative(flask_ctx):
    sample = flask_ctx
    assert template_file_abs_path('/static/uploads/templates/sample.docx') == sample


@pytest.mark.no_app
def test_template_file_abs_path_prod_absolute_fallback_by_basename(flask_ctx):
    sample = flask_ctx
    foreign = '/home/inertia/app/static/uploads/templates/sample.docx'
    assert template_file_abs_path(foreign) == sample


@pytest.mark.no_app
def test_template_file_store_path(flask_ctx):
    sample = flask_ctx
    assert template_file_store_path(sample) == '/static/uploads/templates/sample.docx'
