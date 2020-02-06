"""Definitions for attribute table."""

from .base import BaseDefinitions, InputType
from ..qgis_plugin_tools.tools.i18n import tr

__copyright__ = 'Copyright 2020, 3Liz'
__license__ = 'GPL version 3'
__email__ = 'info@3liz.org'
__revision__ = '$Format:%H$'


class AttributeTableDefinitions(BaseDefinitions):

    def __init__(self):
        super().__init__()
        self._layer_config['layerId'] = {
            'type': InputType.Layer,
            'header': tr('Layer'),
            'default': None,
            'tooltip': tr('The vector layer for the attribute table.')
        }
        self._layer_config['primaryKey'] = {
            'type': InputType.Field,
            'header': tr('Primary key'),
            'default': None,
            'tooltip': tr('Primary key of the layer.')
        }
        self._layer_config['hiddenFields'] = {
            'type': InputType.Fields,
            'header': tr('Fields to hide'),
            'default': '',
            'tooltip': tr('List of fields to hide in the attribute table.')
        }
        self._layer_config['pivot'] = {
            'type': InputType.CheckBox,
            'header': tr('Pivot table'),
            'default': False,
            'tooltip': tr('If the table is a pivot, used in a many-to-many relationship.')
        }
        self._layer_config['hideAsChild'] = {
            'type': InputType.CheckBox,
            'header': tr('Hide in child subpanels'),
            'default': False,
            'tooltip': tr('Do not display the layer in a relation when the layer is a child.')
        }
        self._layer_config['hideLayer'] = {
            'type': InputType.CheckBox,
            'header': tr('Hide layer in list'),
            'default': False,
            'tooltip': tr('No button "Detail" will be shown in Lizmap to open the attribute table, but related features such as selection and filter will be available.')
        }

        self._general_config['limitDataToBbox'] = {
            'type': InputType.CheckBox,
            'default': False,
        }

    @staticmethod
    def primary_keys() -> tuple:
        return 'layerId',

    def key(self) -> str:
        return 'attributeLayers'
