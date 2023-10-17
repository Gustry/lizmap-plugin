""" Filter preview. """
import json

from functools import partial
from pathlib import Path

from qgis.core import Qgis, QgsApplication, QgsProject
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox
from qgis.utils import iface

from lizmap.definitions.filter_by_polygon import FilterLogin, FilterMode
from lizmap.qgis_plugin_tools.tools.i18n import tr
from lizmap.qgis_plugin_tools.tools.resources import load_ui
from lizmap.tools import to_bool

FORM_CLASS = load_ui('ui_preview_filtering.ui')

__copyright__ = 'Copyright 2023, 3Liz'
__license__ = 'GPL version 3'
__email__ = 'info@3liz.org'


class SelectType:
    Identifier = 'identifier'
    NullValues = 'null'
    UnknownIdentifier = 'unknown'


class FilterPreviewDialog(QDialog, FORM_CLASS):

    def __init__(self, groups: dict):
        """ Constructor. """
        QDialog.__init__(self)
        self.setupUi(self)

        accept_button = self.buttons.button(QDialogButtonBox.Ok)
        accept_button.clicked.connect(self.accept)
        cancel_button = self.buttons.button(QDialogButtonBox.Cancel)
        cancel_button.clicked.connect(self.reject)

        for row in FilterLogin:
            self.type_preview.addItem(QIcon(row.value['icon']), row.value['label'], row.value['data'])

        for row in FilterMode:
            self.context.addItem(QIcon(row.value['icon']), row.value['label'], row.value['data'])

        self.type_preview.currentIndexChanged.connect(self.group_or_user)
        self.group_or_user()

        self.input_user.setPlaceholderText("anonymous ('all' values)")
        for group in groups.values():
            self.input_group.addItem(group['label'], group)
        self.input_group.setEditable(True)

        icon = QgsApplication.getThemeIcon("mActionToggleSelectedLayers.svg")
        self.select_identifier.setIcon(icon)
        self.select_identifier.clicked.connect(partial(self.select_preview, SelectType.Identifier))

        icon = QgsApplication.getThemeIcon("mActionHideAllLayers.svg")
        self.select_unvisible.setIcon(icon)
        self.select_unvisible.clicked.connect(partial(self.select_preview, SelectType.NullValues))
        self.select_unknown_group.setIcon(icon)
        self.select_unknown_group.clicked.connect(partial(self.select_preview, SelectType.UnknownIdentifier))

    @property
    def identifier(self) -> str:
        """ Select the current string to use for filtering. """
        if self.type_preview.currentData() == FilterLogin.Group.value['data']:
            return self.input_group.currentData()
        else:
            return self.input_user.text()

    def group_or_user(self):
        """ If we allow the group input. """
        if self.type_preview.currentData() == FilterLogin.Group.value['data']:
            self.label_group.setVisible(True)
            self.label_user.setVisible(False)

            self.input_group.setVisible(True)
            self.input_user.setVisible(False)

            self.select_unknown_group.setVisible(True)
        else:
            self.label_group.setVisible(False)
            self.label_user.setVisible(True)

            self.input_group.setVisible(False)
            self.input_user.setVisible(True)

            self.select_unknown_group.setVisible(False)

    def select_preview(self, select_type: SelectType):
        """ Make the selection. """
        project = QgsProject.instance()
        json_file = Path(project.fileName() + '.cfg')
        with open(json_file, encoding='utf-8') as f:
            json_file_reader = f.read()

        sjson = json.loads(json_file_reader)
        attribute_filtering = sjson.get('loginFilteredLayers')

        filter_type = self.type_preview.currentData()
        filter_context = self.context.currentData()

        for layer in attribute_filtering.values():
            layer_id = layer["layerId"]
            field = layer['filterAttribute']

            vector_layer = project.mapLayer(layer_id)
            vector_layer.removeSelection()

            if filter_context == FilterMode.DisplayEditing.value['data']:
                if to_bool(layer["edition_only"]):
                    vector_layer.selectByExpression("true")
                    continue

            if select_type == SelectType.Identifier:
                expression = (
                    "with_variable("
                    " 'users',"
                    " string_to_array(\"{field}\"),"
                    " array_contains(@users, '{preview}') OR array_contains(@users, 'all')"
                    ")"
                ).format(field=field, preview=self.identifier)
            elif select_type == SelectType.NullValues:
                expression = "\"{field}\" is NULL or \"{field}\" = ''".format(field=field)
            elif select_type == SelectType.UnknownIdentifier:
                expression = "\"{field}\" is NULL or \"{field}\" = ''".format(field=field)
            else:
                raise NotImplementedError("Unknown select type")

            vector_layer.selectByExpression(expression)

        iface.messageBar().pushMessage(
            tr('Filter preview'),
            f"{filter_type} with {self.identifier}, context {self.context.currentData()}",
            level=Qgis.Success,
            duration=5,
        )
