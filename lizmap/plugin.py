__copyright__ = 'Copyright 2023, 3Liz'
__license__ = 'GPL version 3'
__email__ = 'info@3liz.org'

import json
import logging
import os
import re
import sys

from collections import OrderedDict
from functools import partial
from pathlib import Path
from shutil import copyfile
from typing import Dict, Optional, Tuple

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsEditFormConfig,
    QgsExpression,
    QgsLayerTree,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsMapLayer,
    QgsMapLayerModel,
    QgsMapLayerProxyModel,
    QgsProject,
    QgsSettings,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import (
    QCoreApplication,
    QRegExp,
    QStorageInfo,
    Qt,
    QTranslator,
)
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QIcon,
    QPixmap,
    QStandardItem,
)
from qgis.PyQt.QtWidgets import (
    QAction,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidgetItem,
    QTreeWidgetItem,
)

from lizmap import DEFAULT_LWC_VERSION
from lizmap.definitions.atlas import AtlasDefinitions
from lizmap.definitions.attribute_table import AttributeTableDefinitions
from lizmap.definitions.dataviz import DatavizDefinitions, Theme
from lizmap.definitions.definitions import (
    UNSTABLE_VERSION_PREFIX,
    LayerProperties,
    LwcVersions,
    ReleaseStatus,
    ServerComboData,
)
from lizmap.definitions.edition import EditionDefinitions
from lizmap.definitions.filter_by_form import FilterByFormDefinitions
from lizmap.definitions.filter_by_login import FilterByLoginDefinitions
from lizmap.definitions.filter_by_polygon import FilterByPolygonDefinitions
from lizmap.definitions.layouts import LayoutsDefinitions
from lizmap.definitions.locate_by_layer import LocateByLayerDefinitions
from lizmap.definitions.online_help import online_help
from lizmap.definitions.time_manager import TimeManagerDefinitions
from lizmap.definitions.tooltip import ToolTipDefinitions
from lizmap.definitions.warnings import Warnings
from lizmap.dialogs.html_editor import HtmlEditorDialog
from lizmap.dialogs.lizmap_popup import LizmapPopupDialog
from lizmap.dialogs.main import LizmapDialog
from lizmap.dialogs.scroll_message_box import ScrollMessageBox
from lizmap.forms.atlas_edition import AtlasEditionDialog
from lizmap.forms.attribute_table_edition import AttributeTableEditionDialog
from lizmap.forms.dataviz_edition import DatavizEditionDialog
from lizmap.forms.edition_edition import EditionLayerDialog
from lizmap.forms.filter_by_form_edition import FilterByFormEditionDialog
from lizmap.forms.filter_by_login import FilterByLoginEditionDialog
from lizmap.forms.filter_by_polygon import FilterByPolygonEditionDialog
from lizmap.forms.layout_edition import LayoutEditionDialog
from lizmap.forms.locate_layer_edition import LocateLayerEditionDialog
from lizmap.forms.time_manager_edition import TimeManagerEditionDialog
from lizmap.forms.tooltip_edition import ToolTipEditionDialog
from lizmap.lizmap_api.config import LizmapConfig
from lizmap.ogc_project_validity import OgcProjectValidity
from lizmap.saas import is_lizmap_dot_com_hosting, valid_saas_lizmap_dot_com
from lizmap.table_manager.base import TableManager
from lizmap.table_manager.dataviz import TableManagerDataviz
from lizmap.table_manager.layouts import TableManagerLayouts

try:
    from lizmap.plugin_manager import PluginManager
    QGIS_PLUGIN_MANAGER = True
except ModuleNotFoundError:
    # In a standalone application
    QGIS_PLUGIN_MANAGER = False

from lizmap.dialogs.wizard_group import WizardGroupDialog
from lizmap.qgis_plugin_tools.tools.custom_logging import setup_logger
from lizmap.qgis_plugin_tools.tools.ghost_layers import remove_all_ghost_layers
from lizmap.qgis_plugin_tools.tools.i18n import setup_translation, tr
from lizmap.qgis_plugin_tools.tools.resources import (
    plugin_name,
    plugin_path,
    resources_path,
)
from lizmap.qgis_plugin_tools.tools.version import version
from lizmap.qt_style_sheets import NEW_FEATURE_COLOR, NEW_FEATURE_CSS
from lizmap.server_ftp import FtpServer
from lizmap.server_lwc import MAX_DAYS, ServerManager
from lizmap.tools import (
    convert_lizmap_popup,
    current_git_hash,
    format_qgis_version,
    format_version_integer,
    get_layer_wms_parameters,
    has_git,
    layer_property,
    lizmap_user_folder,
    next_git_tag,
    qgis_version,
    to_bool,
    unaccent,
)
from lizmap.tooltip import Tooltip
from lizmap.version_checker import VersionChecker

if qgis_version() >= 31400:
    from qgis.core import QgsProjectServerValidator

LOGGER = logging.getLogger(plugin_name())
VERSION_URL = 'https://raw.githubusercontent.com/3liz/lizmap-web-client/versions/versions.json'
# To try a local file
# VERSION_URL = 'file:///home/etienne/.local/share/QGIS/QGIS3/profiles/default/Lizmap/released_versions.json'


class Lizmap:

    def __init__(self, iface):
        """Constructor of the Lizmap plugin."""
        LOGGER.info("Plugin starting")
        self.iface = iface
        # noinspection PyArgumentList
        self.project = QgsProject.instance()

        # Keep it for a few months
        # 2023/04/15
        QgsSettings().remove('lizmap/instance_target_repository')
        # 04/01/2022
        QgsSettings().remove('lizmap/instance_target_url_authid')

        # Connect the current project filepath
        self.current_path = None
        # noinspection PyUnresolvedReferences
        self.project.fileNameChanged.connect(self.filename_changed)
        self.filename_changed()

        setup_logger(plugin_name())

        _, file_path = setup_translation('lizmap_qgis_plugin_{}.qm', plugin_path('i18n'))

        if file_path:
            self.translator = QTranslator()
            self.translator.load(file_path)
            QCoreApplication.installTranslator(self.translator)

        lizmap_config = LizmapConfig(project=self.project)

        self.dlg = LizmapDialog()
        self.version = version()
        self.version_checker = None
        self.is_dev_version = any(item in self.version for item in UNSTABLE_VERSION_PREFIX)
        self.dlg.label_dev_version.setVisible(False)
        if self.is_dev_version:
            self.dlg.setWindowTitle('Lizmap branch {}, commit {}, next {}'.format(
                self.version, current_git_hash(), next_git_tag()))

            if not has_git():
                text = self.dlg.label_dev_version.text().format(self.version)
                self.dlg.label_dev_version.setText(text)
                self.dlg.label_dev_version.setVisible(True)

        self.layers_table = dict()

        # List of ui widget for data driven actions and checking
        self.global_options = lizmap_config.globalOptionDefinitions
        self.global_options['externalSearch']['widget'] = self.dlg.liExternalSearch

        # List of ui widget for data driven actions and checking
        self.layer_options_list = lizmap_config.layerOptionDefinitions
        self.layer_options_list['legend_image_option']['widget'] = self.dlg.combo_legend_option
        self.layer_options_list['popupSource']['widget'] = self.dlg.liPopupSource
        self.layer_options_list['imageFormat']['widget'] = self.dlg.liImageFormat

        # Fill the combobox from the Lizmap API
        for combo_item in ('legend_image_option', 'popupSource', 'imageFormat', 'externalSearch'):

            item_info = self.layer_options_list.get(combo_item)
            if not item_info:
                item_info = self.global_options.get(combo_item)

            if not item_info:
                # This should not happen
                raise Exception('Unknown type for item_info')

            for option in item_info['list']:
                data, label, tooltip, icon = option
                item_info['widget'].addItem(label, data)
                index = item_info['widget'].findData(data)

                if tooltip:
                    # noinspection PyUnresolvedReferences
                    item_info['widget'].setItemData(index, tooltip, Qt.ToolTipRole)

                if icon:
                    if isinstance(icon, str):
                        # From QGIS resources file
                        pass
                    else:
                        # It's a list, from the plugin
                        icon = resources_path(*icon)
                    item_info['widget'].setItemIcon(index, QIcon(icon))

        # Manage LWC versions combo
        self.dlg.label_lwc_version.setStyleSheet(NEW_FEATURE_CSS)
        self.lwc_versions = OrderedDict()
        self.lwc_versions[LwcVersions.Lizmap_3_1] = []
        self.lwc_versions[LwcVersions.Lizmap_3_2] = [
            self.dlg.label_max_feature_popup,
            self.dlg.label_dataviz,
            self.dlg.label_atlas,
        ]
        self.lwc_versions[LwcVersions.Lizmap_3_3] = [
            self.dlg.label_form_filter,
            self.dlg.btQgisPopupFromForm,
        ]
        self.lwc_versions[LwcVersions.Lizmap_3_4] = [
            self.dlg.label_atlas_34,
            self.dlg.list_group_visibility,
            self.dlg.activate_first_map_theme,
            self.dlg.activate_drawing_tools,
        ]
        self.lwc_versions[LwcVersions.Lizmap_3_5] = [
            self.dlg.liPopupSource.model().item(
                self.dlg.liPopupSource.findData('form')
            ),
            self.dlg.label_filter_polygon,
            self.dlg.filter_polygon_by_user,
            self.dlg.checkbox_scale_overview_map,
        ]
        self.lwc_versions[LwcVersions.Lizmap_3_6] = [
            self.dlg.checkbox_popup_allow_download,
            self.dlg.cb_open_topo_map,
            self.dlg.combo_legend_option.model().item(
                self.dlg.combo_legend_option.findData('expand_at_startup')
            ),
            self.dlg.button_wizard_group_visibility_project,
            self.dlg.button_wizard_group_visibility_layer,
            self.dlg.label_helper_dataviz,
            self.dlg.enable_dataviz_preview,
        ]
        self.lwc_versions[LwcVersions.Lizmap_3_7] = [
            self.dlg.label_layout_panel,
            self.dlg.label_layout_panel_description,
            self.dlg.edit_layout_form_button,
            self.dlg.up_layout_form_button,
            self.dlg.down_layout_form_button,
        ]

        # Add widgets (not done in lizmap_var to avoid dependencies on ui)
        self.global_options['fixed_scale_overview_map']['widget'] = self.dlg.checkbox_scale_overview_map
        self.global_options['mapScales']['widget'] = self.dlg.inMapScales
        self.global_options['minScale']['widget'] = self.dlg.inMinScale
        self.global_options['maxScale']['widget'] = self.dlg.inMaxScale
        self.global_options['acl']['widget'] = self.dlg.inAcl
        self.global_options['initialExtent']['widget'] = self.dlg.inInitialExtent
        self.global_options['googleKey']['widget'] = self.dlg.inGoogleKey
        self.global_options['googleHybrid']['widget'] = self.dlg.cbGoogleHybrid
        self.global_options['googleSatellite']['widget'] = self.dlg.cbGoogleSatellite
        self.global_options['googleTerrain']['widget'] = self.dlg.cbGoogleTerrain
        self.global_options['googleStreets']['widget'] = self.dlg.cbGoogleStreets
        self.global_options['osmMapnik']['widget'] = self.dlg.cbOsmMapnik
        self.global_options['osmStamenToner']['widget'] = self.dlg.cbOsmStamenToner
        self.global_options['openTopoMap']['widget'] = self.dlg.cb_open_topo_map
        self.global_options['bingKey']['widget'] = self.dlg.inBingKey
        self.global_options['bingStreets']['widget'] = self.dlg.cbBingStreets
        self.global_options['bingSatellite']['widget'] = self.dlg.cbBingSatellite
        self.global_options['bingHybrid']['widget'] = self.dlg.cbBingHybrid
        self.global_options['ignKey']['widget'] = self.dlg.inIgnKey
        self.global_options['ignStreets']['widget'] = self.dlg.cbIgnStreets
        self.global_options['ignSatellite']['widget'] = self.dlg.cbIgnSatellite
        self.global_options['ignTerrain']['widget'] = self.dlg.cbIgnTerrain
        self.global_options['ignCadastral']['widget'] = self.dlg.cbIgnCadastral
        self.global_options['hideGroupCheckbox']['widget'] = self.dlg.cbHideGroupCheckbox
        self.global_options['activateFirstMapTheme']['widget'] = self.dlg.activate_first_map_theme
        self.global_options['popupLocation']['widget'] = self.dlg.liPopupContainer
        self.global_options['draw']['widget'] = self.dlg.activate_drawing_tools
        # Deprecated since LWC 3.7.0
        self.global_options['print']['widget'] = self.dlg.cbActivatePrint
        self.global_options['measure']['widget'] = self.dlg.cbActivateMeasure
        self.global_options['zoomHistory']['widget'] = self.dlg.cbActivateZoomHistory
        self.global_options['geolocation']['widget'] = self.dlg.cbActivateGeolocation
        self.global_options['pointTolerance']['widget'] = self.dlg.inPointTolerance
        self.global_options['lineTolerance']['widget'] = self.dlg.inLineTolerance
        self.global_options['polygonTolerance']['widget'] = self.dlg.inPolygonTolerance
        self.global_options['hideHeader']['widget'] = self.dlg.cbHideHeader
        self.global_options['hideMenu']['widget'] = self.dlg.cbHideMenu
        self.global_options['hideLegend']['widget'] = self.dlg.cbHideLegend
        self.global_options['hideOverview']['widget'] = self.dlg.cbHideOverview
        self.global_options['hideNavbar']['widget'] = self.dlg.cbHideNavbar
        self.global_options['hideProject']['widget'] = self.dlg.cbHideProject
        self.global_options['tmTimeFrameSize']['widget'] = self.dlg.inTimeFrameSize
        self.global_options['tmTimeFrameType']['widget'] = self.dlg.liTimeFrameType
        self.global_options['tmAnimationFrameLength']['widget'] = self.dlg.inAnimationFrameLength
        self.global_options['emptyBaselayer']['widget'] = self.dlg.cbAddEmptyBaselayer
        self.global_options['startupBaselayer']['widget'] = self.dlg.cbStartupBaselayer
        self.global_options['limitDataToBbox']['widget'] = self.dlg.cbLimitDataToBbox
        self.global_options['datavizLocation']['widget'] = self.dlg.liDatavizContainer
        self.global_options['datavizTemplate']['widget'] = self.dlg.dataviz_html_template
        self.global_options['theme']['widget'] = self.dlg.combo_theme
        self.global_options['atlasShowAtStartup']['widget'] = self.dlg.atlasShowAtStartup
        self.global_options['atlasAutoPlay']['widget'] = self.dlg.atlasAutoPlay

        self.layer_options_list = lizmap_config.layerOptionDefinitions
        # Add widget information
        self.layer_options_list['title']['widget'] = self.dlg.inLayerTitle
        self.layer_options_list['abstract']['widget'] = self.dlg.teLayerAbstract
        self.layer_options_list['link']['widget'] = self.dlg.inLayerLink
        self.layer_options_list['minScale']['widget'] = None
        self.layer_options_list['maxScale']['widget'] = None
        self.layer_options_list['toggled']['widget'] = self.dlg.cbToggled
        self.layer_options_list['group_visibility']['widget'] = self.dlg.list_group_visibility
        self.layer_options_list['popup']['widget'] = self.dlg.checkbox_popup
        self.layer_options_list['popupFrame']['widget'] = self.dlg.popup_frame
        self.layer_options_list['popupTemplate']['widget'] = None
        self.layer_options_list['popupMaxFeatures']['widget'] = self.dlg.sbPopupMaxFeatures
        self.layer_options_list['popupDisplayChildren']['widget'] = self.dlg.cbPopupDisplayChildren
        self.layer_options_list['popup_allow_download']['widget'] = self.dlg.checkbox_popup_allow_download
        self.layer_options_list['groupAsLayer']['widget'] = self.dlg.cbGroupAsLayer
        self.layer_options_list['baseLayer']['widget'] = self.dlg.cbLayerIsBaseLayer
        self.layer_options_list['displayInLegend']['widget'] = self.dlg.cbDisplayInLegend
        self.layer_options_list['singleTile']['widget'] = self.dlg.cbSingleTile
        self.layer_options_list['cached']['widget'] = self.dlg.checkbox_server_cache
        self.layer_options_list['serverFrame']['widget'] = self.dlg.server_cache_frame
        self.layer_options_list['cacheExpiration']['widget'] = self.dlg.inCacheExpiration
        self.layer_options_list['metatileSize']['widget'] = self.dlg.inMetatileSize
        self.layer_options_list['clientCacheExpiration']['widget'] = self.dlg.inClientCacheExpiration
        self.layer_options_list['externalWmsToggle']['widget'] = self.dlg.cbExternalWms
        self.layer_options_list['sourceRepository']['widget'] = self.dlg.inSourceRepository
        self.layer_options_list['sourceProject']['widget'] = self.dlg.inSourceProject

        # Disabled because done earlier
        # self.layer_options_list['legend_image_option']['widget'] = self.dlg.combo_legend_option
        # self.layer_options_list['popupSource']['widget'] = self.dlg.liPopupSource
        # self.layer_options_list['imageFormat']['widget'] = self.dlg.liImageFormat
        # self.global_options['externalSearch']['widget'] = self.dlg.liExternalSearch

        # map QGIS geometry type
        # TODO lizmap 4, to remove
        self.mapQgisGeometryType = {
            0: 'point',
            1: 'line',
            2: 'polygon',
            3: 'unknown',
            4: 'none'
        }

        # Disable checkboxes on the layer tab
        self.enable_check_box(False)

        # Disable deprecated lizmap functions #121
        self.dlg.gb_lizmapExternalBaselayers.setVisible(False)

        # Catch user interaction on layer tree and inputs
        self.dlg.layer_tree.itemSelectionChanged.connect(self.from_data_to_ui_for_layer_group)

        # Catch user interaction on Map Scales input
        self.dlg.inMapScales.editingFinished.connect(self.get_min_max_scales)

        warning_icon = QPixmap(":images/themes/default/mIconWarning.svg")

        # Scales
        self.dlg.min_scale_pic.setPixmap(QPixmap(":images/themes/default/mActionZoomOut.svg"))
        self.dlg.min_scale_pic.setText('')
        self.dlg.max_scale_pic.setPixmap(QPixmap(":images/themes/default/mActionZoomIn.svg"))
        self.dlg.max_scale_pic.setText('')
        self.dlg.label_warning_crs.setPixmap(warning_icon)
        ui_items = (
            self.dlg.label_min_scale, self.dlg.label_max_scale,
            self.dlg.min_scale_pic, self.dlg.max_scale_pic,
            self.dlg.inMinScale, self.dlg.inMaxScale,
        )
        for item in ui_items:
            item.setToolTip(tr("The minimum and maximum scales are defined by your minimum and maximum values above."))

        self.dlg.image_warning_project.setText("")
        self.dlg.image_warning_project.setPixmap(warning_icon)

        # Popup configuration
        self.dlg.image_warning_lizmap_popup.setPixmap(warning_icon)
        self.dlg.image_warning_lizmap_popup.setText('')

        widget_source_popup = self.layer_options_list['popupSource']['widget']
        widget_source_popup.currentIndexChanged.connect(self.enable_popup_source_button)

        index = widget_source_popup.findData('form')
        form_popup = widget_source_popup.model().item(index)

        font = form_popup.font()
        font.setUnderline(True)
        form_popup.setFont(font)

        # Connect widget signals to setLayerProperty method depending on widget type
        for key, item in self.layer_options_list.items():
            if item.get('widget'):
                control = item['widget']
                slot = partial(self.save_value_layer_group_data, key)
                if item['wType'] in ('text', 'spinbox'):
                    control.editingFinished.connect(slot)
                elif item['wType'] == 'textarea':
                    control.textChanged.connect(slot)
                elif item['wType'] == 'checkbox':
                    control.stateChanged.connect(slot)
                elif item['wType'] == 'list':
                    control.currentIndexChanged.connect(slot)
                elif item['wType'] == 'layers':
                    control.layerChanged.connect(slot)
                elif item['wType'] == 'fields':
                    control.fieldChanged.connect(slot)

        self.crs_3857_base_layers_list = {
            'osm-mapnik': self.dlg.cbOsmMapnik,
            'osm-stamen-toner': self.dlg.cbOsmStamenToner,
            'opentopomap': self.dlg.cb_open_topo_map,
            'google-street': self.dlg.cbGoogleStreets,
            'google-satellite': self.dlg.cbGoogleSatellite,
            'google-hybrid': self.dlg.cbGoogleHybrid,
            'google-terrain': self.dlg.cbGoogleTerrain,
            'bing-road': self.dlg.cbBingStreets,
            'bing-aerial': self.dlg.cbBingSatellite,
            'bing-hybrid': self.dlg.cbBingHybrid,
            'ign-plan': self.dlg.cbIgnStreets,
            'ign-photo': self.dlg.cbIgnSatellite,
            'ign-scan': self.dlg.cbIgnTerrain,
            'ign-cadastral': self.dlg.cbIgnCadastral,
        }
        for item in self.crs_3857_base_layers_list.values():
            slot = self.check_visibility_crs_3857
            item.stateChanged.connect(slot)
        self.check_visibility_crs_3857()

        # Connect base-layer checkboxes
        self.base_layer_widget_list = {
            'layer': self.dlg.cbLayerIsBaseLayer,
            'empty': self.dlg.cbAddEmptyBaselayer
        }
        self.base_layer_widget_list.update(self.crs_3857_base_layers_list)
        for item in self.base_layer_widget_list.values():
            slot = self.on_baselayer_checkbox_change
            item.stateChanged.connect(slot)

        self.server_ftp = FtpServer(self.dlg)

        self.server_manager = ServerManager(
            self.dlg,
            self.dlg.table_server,
            self.dlg.add_server_button,
            self.dlg.add_first_server,
            self.dlg.remove_server_button,
            self.dlg.edit_server_button,
            self.dlg.refresh_versions_button,
            self.dlg.move_up_server_button,
            self.dlg.move_down_server_button,
            self.check_dialog_validity,
        )
        # Debug
        # self.server_manager.clean_cache(True)

        current = format_qgis_version(qgis_version())
        current = '{}.{}'.format(current[0], current[1])
        self.dlg.label_current_qgis.setText('<b>{}</b>'.format(current))
        text = self.dlg.qgis_and_lwc_versions_issue.text()
        self.dlg.qgis_and_lwc_versions_issue.setText(text.format(version=current))
        self.dlg.qgis_and_lwc_versions_issue.setVisible(False)

        # tables of layers
        # Todo Lizmap 3.4, remove dict init here
        self.layers_table = {
            'atlas': {
                'tableWidget': self.dlg.table_atlas,
                'removeButton': self.dlg.button_atlas_remove,
                'addButton': self.dlg.button_atlas_add,
                'editButton': self.dlg.button_atlas_edit,
                'upButton': self.dlg.button_atlas_up,
                'downButton': self.dlg.button_atlas_down,
                'manager': None,
            },
            'locateByLayer': {
                'tableWidget': self.dlg.table_locate_by_layer,
                'removeButton': self.dlg.remove_locate_layer_button,
                'addButton': self.dlg.add_locate_layer_button,
                'editButton': self.dlg.edit_locate_layer_button,
                'upButton': self.dlg.up_locate_layer_button,
                'downButton': self.dlg.down_locate_layer_button,
                'manager': None,
            },
            'attributeLayers': {
                'tableWidget': self.dlg.table_attribute_table,
                'removeButton': self.dlg.remove_attribute_table_button,
                'addButton': self.dlg.add_attribute_table_button,
                'editButton': self.dlg.edit_attribute_table_button,
                'upButton': self.dlg.up_attribute_table_button,
                'downButton': self.dlg.down_attribute_table_button,
                'manager': None,
            },
            'tooltipLayers': {
                'tableWidget': self.dlg.table_tooltip,
                'removeButton': self.dlg.remove_tooltip_button,
                'addButton': self.dlg.add_tooltip_button,
                'editButton': self.dlg.edit_tooltip_button,
                'upButton': self.dlg.up_tooltip_button,
                'downButton': self.dlg.down_tooltip_button,
                'manager': None,
            },
            'editionLayers': {
                'tableWidget': self.dlg.edition_table,
                'removeButton': self.dlg.remove_edition_layer,
                'addButton': self.dlg.add_edition_layer,
                'editButton': self.dlg.edit_edition_layer,
                'upButton': self.dlg.up_edition_layer,
                'downButton': self.dlg.down_edition_layer,
                'manager': None,
            },
            'layouts': {
                'tableWidget': self.dlg.table_layout,
                'editButton': self.dlg.edit_layout_form_button,
                'upButton': self.dlg.up_layout_form_button,
                'downButton': self.dlg.down_layout_form_button,
                'manager': None,
            },
            'loginFilteredLayers': {
                'tableWidget': self.dlg.table_login_filter,
                'removeButton': self.dlg.remove_filter_login_layer_button,
                'addButton': self.dlg.add_filter_login_layer_button,
                'editButton': self.dlg.edit_filter_login_layer_button,
                'manager': None,
            },
            'lizmapExternalBaselayers': {
                'tableWidget': self.dlg.twLizmapBaselayers,
                'removeButton': self.dlg.btLizmapBaselayerDel,
                'addButton': self.dlg.btLizmapBaselayerAdd,
                'cols': ['repository', 'project', 'layerName', 'layerTitle', 'layerImageFormat', 'order'],
                'jsonConfig': {}
            },
            'timemanagerLayers': {
                'tableWidget': self.dlg.time_manager_table,
                'removeButton': self.dlg.remove_time_manager_layer,
                'addButton': self.dlg.add_time_manager_layer,
                'editButton': self.dlg.edit_time_manager_layer,
                'upButton': self.dlg.up_time_manager_layer,
                'downButton': self.dlg.down_time_manager_layer,
                'manager': None,
            },
            'datavizLayers': {
                'tableWidget': self.dlg.table_dataviz,
                'removeButton': self.dlg.remove_dataviz_layer,
                'addButton': self.dlg.add_dataviz_layer,
                'editButton': self.dlg.edit_dataviz_layer,
                'upButton': self.dlg.up_dataviz_layer,
                'downButton': self.dlg.down_dataviz_layer,
                'manager': None,
            },
            'filter_by_polygon': {
                'tableWidget': self.dlg.table_filter_polygon,
                'removeButton': self.dlg.remove_filter_polygon_button,
                'addButton': self.dlg.add_filter_polygon_button,
                'editButton': self.dlg.edit_filter_polygon_button,
                'manager': None,
            },
            'formFilterLayers': {
                'tableWidget': self.dlg.table_form_filter,
                'removeButton': self.dlg.remove_filter_form_button,
                'addButton': self.dlg.add_filter_form_button,
                'editButton': self.dlg.edit_filter_form_button,
                'upButton': self.dlg.up_filter_form_button,
                'downButton': self.dlg.down_filter_form_button,
                'manager': None,
            }
        }

        # Set some tooltips
        tooltip = tr(
            'By default the layer is visible for all groups in Lizmap.\n'
            'If a comma separated list of groups IDs is defined,\n'
            'the layer will be visible only for these groups.\n'
            'Use Lizmap Web Client group IDs and not labels.')
        self.dlg.label_group_visibility.setToolTip(tooltip)
        self.dlg.list_group_visibility.setToolTip(tooltip)

        self.dlg.button_generate_html_table.setToolTip(tr(
            "A default HTML table will be generated in the layer maptip. The layout will be very similar to the auto "
            "popup, except that the display of a media must still be managed manually using HTML &lt;a&gt; or "
            "&lt;img&gt; for instance."
        ))

        # Filter by polygon
        self.dlg.layer_filter_polygon.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.dlg.layer_filter_polygon.layerChanged.connect(self.dlg.field_filter_polygon.setLayer)
        self.dlg.field_filter_polygon.setLayer(self.dlg.layer_filter_polygon.currentLayer())

        # Server combo
        server = QgsSettings().value('lizmap/instance_target_url', '')
        if server:
            index = self.dlg.server_combo.findData(server, ServerComboData.ServerUrl.value)
            if index:
                self.dlg.server_combo.setCurrentIndex(index)
        self.dlg.server_combo.currentIndexChanged.connect(self.target_server_changed)
        self.dlg.repository_combo.currentIndexChanged.connect(self.target_repository_changed)
        self.target_server_changed()
        self.dlg.refresh_combo_repositories()

        self.layerList = None
        self.action = None
        self.embeddedGroups = None
        self.myDic = None
        self.help_action = None

    def filename_changed(self):
        """ When the current project has been renamed. """
        if os.getenv("QGIS_PLUGIN_AUTO_SAVING"):
            # Special variable set from QGIS autoSaver plugin
            return

        if not self.project.absoluteFilePath():
            return

        new_path = Path(self.project.absoluteFilePath())
        if str(new_path).endswith('.bak'):
            # We skip, it's from the QGIS plugin autoSaver
            return

        if self.current_path and new_path != self.current_path and not os.getenv("CI"):
            old_cfg = self.current_path.with_suffix('.qgs.cfg')
            if old_cfg.exists():
                box = QMessageBox(self.dlg)
                box.setIcon(QMessageBox.Question)
                box.setWindowIcon(QIcon(resources_path('icons', 'icon.png')), )
                box.setWindowTitle(tr('Project has been renamed'))
                box.setText(tr(
                    'The previous project located at "{}" was associated to a Lizmap configuration. '
                    'Do you want to copy the previous Lizmap configuration file to this new project ?'
                ).format(self.current_path))
                box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                box.setDefaultButton(QMessageBox.No)
                result = box.exec_()
                if result == QMessageBox.No:
                    return

                copyfile(str(old_cfg), str(new_path.with_suffix('.qgs.cfg')))
                LOGGER.info("Project has been renamed and CFG file has been copied as well.")

        self.current_path = new_path

    def target_server_changed(self):
        """ When the server destination has changed in the selector. """
        current_authid = self.dlg.server_combo.currentData(ServerComboData.AuthId.value)
        current_url = self.dlg.server_combo.currentData(ServerComboData.ServerUrl.value)
        QgsSettings().setValue('lizmap/instance_target_url', current_url)
        QgsSettings().setValue('lizmap/instance_target_url_authid', current_authid)
        self.check_dialog_validity()
        self.dlg.refresh_combo_repositories()

        current_version = self.dlg.current_lwc_version()
        old_version = QgsSettings().value(
            'lizmap/lizmap_web_client_version', DEFAULT_LWC_VERSION.value, str)
        if current_version != old_version:
            self.lwc_version_changed()
        # self.dlg.check_qgis_version()

    def target_repository_changed(self):
        """ When the repository destination has changed in the selector. """
        # The new repository is only set when we save the CFG file
        # Otherwise, it will make a mess with the signals about the last repository used and the server refreshed list
        if self.dlg.page_dataviz.isVisible():
            self.layers_table['datavizLayers'].get('manager').preview_dataviz_dialog()

    def lwc_version_changed(self):
        """When the version has changed in the selector."""
        current_version = self.dlg.current_lwc_version()

        if current_version is None:
            # We come from a higher version of Lizmap (from dev to master)
            current_version = DEFAULT_LWC_VERSION

        LOGGER.debug("Saving new value about the LWC target version : {}".format(current_version.value))
        QgsSettings().setValue('lizmap/lizmap_web_client_version', str(current_version.value))

        # New print panel
        # The checkbox is deprecated since LWC 3.7.0
        self.dlg.cbActivatePrint.setVisible(current_version <= LwcVersions.Lizmap_3_6)
        self.dlg.cbActivatePrint.setEnabled(current_version <= LwcVersions.Lizmap_3_6)

        found = False
        for lwc_version, items in self.lwc_versions.items():
            if found:
                for item in items:
                    if isinstance(item, QLabel):
                        item.setStyleSheet(NEW_FEATURE_CSS)
                    elif isinstance(item, QStandardItem):
                        # QComboBox
                        brush = QBrush()
                        # noinspection PyUnresolvedReferences
                        brush.setStyle(Qt.SolidPattern)
                        brush.setColor(QColor(NEW_FEATURE_COLOR))
                        item.setBackground(brush)
            else:
                for item in items:
                    if isinstance(item, QLabel):
                        item.setStyleSheet('')
                    elif isinstance(item, QStandardItem):
                        # QComboBox
                        item.setBackground(QBrush())

            if lwc_version == current_version:
                found = True

        # Change in all table manager too
        for key in self.layers_table.keys():
            manager = self.layers_table[key].get('manager')
            if manager:
                manager.set_lwc_version(current_version)

        # Compare the LWC version with the current QGIS Desktop version and the release JSON file
        version_file = lizmap_user_folder().joinpath('released_versions.json')
        if not version_file.exists():
            return

        with open(version_file, encoding='utf8') as json_file:
            json_content = json.loads(json_file.read())

        for lzm_version in json_content:
            if lzm_version['branch'] != current_version.value:
                continue

            qgis_min = lzm_version.get('qgis_min_version_recommended')
            qgis_max = lzm_version.get('qgis_max_version_recommended')
            if not (qgis_min or qgis_max):
                break

            if qgis_min <= qgis_version() < qgis_max:
                self.dlg.qgis_and_lwc_versions_issue.setVisible(False)
            else:
                self.dlg.qgis_and_lwc_versions_issue.setVisible(True)

    # noinspection PyPep8Naming
    def initGui(self):
        """Create action that will start plugin configuration"""
        LOGGER.debug("Plugin starting in the initGui")
        icon = QIcon(resources_path('icons', 'icon.png'))
        self.action = QAction(icon, 'Lizmap', self.iface.mainWindow())

        # connect the action to the run method
        # noinspection PyUnresolvedReferences
        self.action.triggered.connect(self.run)

        # Open the online help
        self.help_action = QAction(icon, 'Lizmap', self.iface.mainWindow())
        self.iface.pluginHelpMenu().addAction(self.help_action)
        # noinspection PyUnresolvedReferences
        self.help_action.triggered.connect(self.show_help)

        # connect Lizmap signals and functions
        self.dlg.buttonBox.button(QDialogButtonBox.Cancel).clicked.connect(self.dlg.close)
        self.dlg.buttonBox.button(QDialogButtonBox.Apply).clicked.connect(self.save_cfg_file)
        self.dlg.buttonBox.button(QDialogButtonBox.Ok).clicked.connect(self.ok_button_clicked)
        self.dlg.buttonBox.button(QDialogButtonBox.Help).clicked.connect(self.show_help)

        # Connect the left menu to the right panel
        self.dlg.mOptionsListWidget.currentRowChanged.connect(self.dlg.mOptionsStackedWidget.setCurrentIndex)

        # clear log button clicked
        self.dlg.button_clear_log.clicked.connect(self.clear_log)

        # Group wizard
        icon = QIcon(resources_path('icons', 'user_group.svg'))
        self.dlg.button_wizard_group_visibility_project.setText('')
        self.dlg.button_wizard_group_visibility_layer.setText('')
        self.dlg.button_wizard_group_visibility_project.setIcon(icon)
        self.dlg.button_wizard_group_visibility_layer.setIcon(icon)
        self.dlg.button_wizard_group_visibility_project.clicked.connect(self.open_wizard_group_project)
        self.dlg.button_wizard_group_visibility_layer.clicked.connect(self.open_wizard_group_layer)
        tooltip = tr("Open the group wizard")
        self.dlg.button_wizard_group_visibility_project.setToolTip(tooltip)
        self.dlg.button_wizard_group_visibility_layer.setToolTip(tooltip)

        # configure popup button
        self.dlg.btConfigurePopup.setText('')
        self.dlg.btConfigurePopup.setIcon(QIcon(":images/themes/default/console/iconSettingsConsole.svg"))
        self.dlg.btConfigurePopup.clicked.connect(self.configure_html_popup)
        self.dlg.convert_html_maptip.clicked.connect(self.convert_html_maptip)
        self.dlg.btQgisPopupFromForm.clicked.connect(self.maptip_from_form)
        self.dlg.button_generate_html_table.clicked.connect(self.html_table_from_layer)
        self.dlg.widget_deprecated_lizmap_popup.setVisible(False)

        # Link button
        self.dlg.button_refresh_link.setIcon(QIcon(QgsApplication.iconPath('mActionRefresh.svg')))
        self.dlg.button_refresh_link.setText('')
        self.dlg.button_refresh_link.setToolTip('Set the link from the dataUrl property in the layer properties.')
        self.dlg.button_refresh_link.clicked.connect(self.link_from_properties)

        # detect project closed
        self.iface.projectRead.connect(self.on_project_read)
        self.iface.newProjectCreated.connect(self.on_project_read)

        # initial extent
        self.dlg.btSetExtentFromProject.clicked.connect(self.set_initial_extent_from_project)
        self.dlg.btSetExtentFromCanvas.clicked.connect(self.set_initial_extent_from_canvas)

        self.dlg.btSetExtentFromProject.setIcon(QIcon(":images/themes/default/propertyicons/overlay.svg"))
        self.dlg.btSetExtentFromCanvas.setIcon(QIcon(":images/themes/default/mLayoutItemMap.svg"))

        # Dataviz options
        for item in Theme:
            self.global_options['theme']['widget'].addItem(item.value["label"], item.value["data"])
        index = self.global_options['theme']['widget'].findData(Theme.Light.value["data"])
        self.global_options['theme']['widget'].setCurrentIndex(index)

        # Manage "delete line" button
        for key, item in self.layers_table.items():
            control = item.get('removeButton')
            if control:
                slot = partial(self.remove_selected_layer_from_table, key)
                control.clicked.connect(slot)
                # noinspection PyCallByClass,PyArgumentList
                control.setIcon(QIcon(QgsApplication.iconPath('symbologyRemove.svg')))
                control.setText('')
                control.setToolTip(tr('Remove the selected layer from the list'))

            control = item.get('addButton')
            if control:
                control.setText('')
                # noinspection PyCallByClass,PyArgumentList
                control.setIcon(QIcon(QgsApplication.iconPath('symbologyAdd.svg')))
                control.setToolTip(tr('Add a new layer in the list'))

            control = item.get('editButton')
            if control:
                # If there is an edit button, it's the new generation of form
                slot = partial(self.edit_layer, key)
                control.clicked.connect(slot)
                control.setText('')
                # noinspection PyCallByClass,PyArgumentList
                control.setIcon(QIcon(QgsApplication.iconPath('symbologyEdit.svg')))
                control.setToolTip(tr('Edit the current layer configuration'))

                slot = partial(self.add_new_layer, key)
                add_button = item.get('addButton')
                if add_button:
                    add_button.clicked.connect(slot)
                if key == 'atlas':
                    definition = AtlasDefinitions()
                    dialog = AtlasEditionDialog
                elif key == 'attributeLayers':
                    definition = AttributeTableDefinitions()
                    dialog = AttributeTableEditionDialog
                elif key == 'editionLayers':
                    definition = EditionDefinitions()
                    dialog = EditionLayerDialog
                elif key == 'datavizLayers':
                    definition = DatavizDefinitions()
                    dialog = DatavizEditionDialog
                elif key == 'layouts':
                    definition = LayoutsDefinitions()
                    dialog = LayoutEditionDialog
                elif key == 'locateByLayer':
                    definition = LocateByLayerDefinitions()
                    dialog = LocateLayerEditionDialog
                elif key == 'loginFilteredLayers':
                    definition = FilterByLoginDefinitions()
                    dialog = FilterByLoginEditionDialog
                elif key == 'timemanagerLayers':
                    definition = TimeManagerDefinitions()
                    dialog = TimeManagerEditionDialog
                elif key == 'tooltipLayers':
                    definition = ToolTipDefinitions()
                    dialog = ToolTipEditionDialog
                elif key == 'formFilterLayers':
                    definition = FilterByFormDefinitions()
                    dialog = FilterByFormEditionDialog
                elif key == 'filter_by_polygon':
                    definition = FilterByPolygonDefinitions()
                    dialog = FilterByPolygonEditionDialog
                else:
                    raise Exception('Unknown panel.')

                item['tableWidget'].horizontalHeader().setStretchLastSection(True)

                if key == 'datavizLayers':
                    # noinspection PyTypeChecker
                    item['manager'] = TableManagerDataviz(
                        self.dlg,
                        definition,
                        dialog,
                        item['tableWidget'],
                        item['editButton'],
                        item.get('upButton'),
                        item.get('downButton'),
                    )
                elif key == 'layouts':
                    # noinspection PyTypeChecker
                    item['manager'] = TableManagerLayouts(
                        self.dlg,
                        definition,
                        dialog,
                        item['tableWidget'],
                        item['editButton'],
                        item.get('upButton'),
                        item.get('downButton'),
                    )
                else:
                    # noinspection PyTypeChecker
                    item['manager'] = TableManager(
                        self.dlg,
                        definition,
                        dialog,
                        item['tableWidget'],
                        item['removeButton'],
                        item['editButton'],
                        item.get('upButton'),
                        item.get('downButton'),
                    )

                control = item.get('upButton')
                if control:
                    slot = partial(self.move_layer_up, key)
                    control.clicked.connect(slot)
                    control.setText('')
                    # noinspection PyCallByClass,PyArgumentList
                    control.setIcon(QIcon(QgsApplication.iconPath('mActionArrowUp.svg')))
                    control.setToolTip(tr('Move the layer up in the table'))

                control = item.get('downButton')
                if control:
                    slot = partial(self.move_layer_down, key)
                    control.clicked.connect(slot)
                    control.setText('')
                    # noinspection PyCallByClass,PyArgumentList
                    control.setIcon(QIcon(QgsApplication.iconPath('mActionArrowDown.svg')))
                    control.setToolTip(tr('Move the layer down in the table'))

        # Delete layers from table when deleted from registry
        # noinspection PyUnresolvedReferences
        self.project.layersRemoved.connect(self.remove_layer_from_table_by_layer_ids)

        # Layouts
        # Not connecting the "layoutAdded" signal, it's done when opening the Lizmap plugin
        # noinspection PyUnresolvedReferences
        self.project.layoutManager().layoutRenamed.connect(self.layout_renamed)
        # noinspection PyUnresolvedReferences
        self.project.layoutManager().layoutRemoved.connect(self.layout_removed)

        # Lizmap external layers as baselayers
        # add a layer to the lizmap external baselayers
        self.dlg.btLizmapBaselayerAdd.clicked.connect(self.addLayerToLizmapBaselayers)

        # Atlas
        self.dlg.label_atlas_34.setVisible(self.is_dev_version)

        self.iface.addPluginToWebMenu(None, self.action)
        self.iface.addWebToolBarIcon(self.action)

        server_side = tr(
            "This value will be replaced on the server side when evaluating the expression thanks to "
            "the QGIS server Lizmap plugin.")
        # Register variable helps
        if qgis_version() >= 32200:
            QgsExpression.addVariableHelpText(
                "lizmap_user",
                "{}<br/>{}<br/>{}".format(
                    tr("The current Lizmap login as a string."),
                    tr("It might be an empty string if the user is not connected."),
                    server_side,
                )
            )
            QgsExpression.addVariableHelpText(
                "lizmap_user_groups",
                "{}<br/>{}<br/>{}".format(
                    tr("The current groups of the logged user as an array."),
                    tr("It might be an empty array if the user is not connected."),
                    server_side,
                )
            )
            QgsExpression.addVariableHelpText("lizmap_repository", tr("The current repository ID on the server."))

        # Let's fix the dialog to the first panel
        self.dlg.mOptionsListWidget.setCurrentRow(0)

    def check_dialog_validity(self) -> bool:
        """ Check the global dialog validity if we have :
         * at least one server
         * all servers with a login associated
         * LWC 3.5 doesn't check the status of QGIS server
         * LWC 3.6 must have a valid QGIS server setup
         * a QGS project

        Only the first tab is always allowed.
        All other tabs must have these conditions.

        Returns True if all tabs are available.
        """
        # Check the current selected server in the combobox
        if not self.dlg.server_combo.currentData(ServerComboData.ServerUrl.value):
            msg = tr('Please add your Lizmap server in the table below.')
            self.dlg.allow_navigation(False, msg)
            return False

        valid, msg = self.check_global_project_options()
        if not valid:
            self.dlg.allow_navigation(False, msg)
            return False

        # Project is valid, now check the server table validity
        # Somehow in tests, we don't have the variable
        if hasattr(self, 'server_manager') and not self.server_manager.check_validity_servers():
            msg = tr(
                'You must have all Lizmap servers with a valid URL and a login provided before using the plugin.'
            )
            self.dlg.allow_navigation(False, msg)
            return False

        metadata = self.dlg.server_combo.currentData(ServerComboData.JsonMetadata.value)
        if not metadata:
            msg = tr(
                'The selected server in the combobox must be reachable. The server has not been reachable for {number} '
                'days.'
            ).format(number=MAX_DAYS)
            self.dlg.allow_navigation(False, msg)
            return False

        self.dlg.allow_navigation(True)
        return True

    def add_new_layer(self, key):
        self.layers_table[key]['manager'].add_new_row()

    def move_layer_up(self, key):
        self.layers_table[key]['manager'].move_layer_up()

    def move_layer_down(self, key):
        self.layers_table[key]['manager'].move_layer_down()

    def edit_layer(self, key):
        self.layers_table[key]['manager'].edit_existing_row()

    def unload(self):
        """Remove the plugin menu item and icon."""
        self.iface.webMenu().removeAction(self.action)
        self.iface.removeWebToolBarIcon(self.action)

        if self.help_action:
            self.iface.pluginHelpMenu().removeAction(self.help_action)
            del self.help_action

    def enable_popup_source_button(self):
        """Enable or not the "Configure" button according to the popup source."""
        data = self.layer_options_list['popupSource']['widget'].currentData()
        self.dlg.btConfigurePopup.setVisible(data in ('lizmap', 'qgis'))
        self.dlg.widget_qgis_maptip.setVisible(data == 'qgis')

        if data == 'lizmap':
            layer = self._current_selected_layer()
            self.dlg.widget_deprecated_lizmap_popup.setVisible(isinstance(layer, QgsVectorLayer))
        else:
            self.dlg.widget_deprecated_lizmap_popup.setVisible(False)

    def open_wizard_group_layer(self):
        """ Open the group wizard for the layer visibility. """
        line_edit = self.dlg.list_group_visibility
        layer = self._current_selected_layer()
        if not layer:
            return
        helper = tr("Setting groups for the layer visibility '{}'").format(layer.name())
        self._open_wizard_group(line_edit, helper)
        # Trigger saving of the new value
        self.save_value_layer_group_data('group_visibility')

    def open_wizard_group_project(self):
        """ Open the group wizard for the project visibility. """
        line_edit = self.dlg.inAcl
        helper = tr("Setting groups for the project visibility.")
        self._open_wizard_group(line_edit, helper)

    def _open_wizard_group(self, line_edit: QLineEdit, helper: str) -> Optional[str]:
        """ Open the group wizard and set the output in the line edit. """
        # Duplicated in base_edition_dialog.py, open_wizard_dialog()
        json_metadata = self.dlg.server_combo.currentData(ServerComboData.JsonMetadata.value)
        acl = json_metadata.get('acl')
        if not acl:
            QMessageBox.critical(
                self.dlg,
                tr('Upgrade your Lizmap instance'),
                tr(
                    "Your current Lizmap instance, running version {}, is not providing the needed information. "
                    "You should upgrade your Lizmap instance to at least 3.6.1 to use this wizard."
                ).format(json_metadata["info"]["version"]),
                QMessageBox.Ok
            )
            return None
        # End of duplicated

        current_acl = line_edit.text()
        wizard_dialog = WizardGroupDialog(helper, current_acl, acl['groups'])
        if not wizard_dialog.exec_():
            return None

        text = wizard_dialog.preview.text()
        if not text:
            return

        line_edit.setText(text)

    @staticmethod
    def show_help():
        """Opens the html help file content with default browser."""
        # noinspection PyArgumentList
        QDesktopServices.openUrl(online_help())

    @staticmethod
    def log(msg, abort=None, textarea=None):
        """Log the actions and errors and optionally show them in given text area."""
        if abort:
            sys.stdout = sys.stderr
        if textarea:
            textarea.append(msg)

    def clear_log(self):
        """Clear the content of the text area log."""
        self.dlg.outLog.clear()

    def enable_check_box(self, value):
        """Enable/Disable checkboxes and fields of the Layer tab."""
        for key, item in self.layer_options_list.items():
            if item.get('widget') and key != 'sourceProject':
                item['widget'].setEnabled(value)
        self.dlg.btConfigurePopup.setEnabled(value)
        self.dlg.btQgisPopupFromForm.setEnabled(value)
        self.dlg.button_generate_html_table.setEnabled(value)

    def get_min_max_scales(self):
        """Get Min Max Scales from scales input field."""
        LOGGER.info('Getting min/max scales')
        min_scale = 1
        max_scale = 1000000000
        in_map_scales = self.dlg.inMapScales.text()
        map_scales = [int(a.strip(' \t')) for a in in_map_scales.split(',') if str(a.strip(' \t')).isdigit()]
        map_scales.sort()
        if len(map_scales) < 2:
            QMessageBox.critical(
                self.dlg,
                tr('Lizmap Error'),
                tr(
                    'Map scales: Write down integer scales separated by comma. '
                    'You must enter at least 2 min and max values.'),
                QMessageBox.Ok)
        else:
            min_scale = min(map_scales)
            max_scale = max(map_scales)
        self.dlg.inMinScale.setText(str(min_scale))
        self.dlg.inMaxScale.setText(str(max_scale))
        self.dlg.inMapScales.setText(', '.join(map(str, map_scales)))

    def read_cfg_file(self, skip_tables=False) -> dict:
        """Get the saved configuration from the project.qgs.cfg config file.

        Populate the gui fields accordingly

        skip_tables is only used in tests, as we don't have "table managers". It's only for testing the "layer" panel.
        """
        json_options = {}
        json_file = self.dlg.cfg_file()
        if json_file.exists():
            with open(json_file, encoding='utf-8') as f:
                json_file_reader = f.read()

            # noinspection PyBroadException
            try:
                sjson = json.loads(json_file_reader)
                json_options = sjson['options']
                for key in self.layers_table.keys():
                    if key in sjson:
                        self.layers_table[key]['jsonConfig'] = sjson[key]
                    else:
                        self.layers_table[key]['jsonConfig'] = {}

                    manager = self.layers_table[key].get('manager')
                    if manager:

                        manager.truncate()

                        if key == 'layouts':
                            manager.load_qgis_layouts(sjson.get(key, {}))
                            continue

                        if key in sjson:
                            manager.from_json(sjson[key])
                        else:
                            # get a subset of the data to give to the table form
                            data = {k: json_options[k] for k in json_options if k.startswith(manager.definitions.key())}
                            if data:
                                manager.from_json(data)

            except Exception as e:
                if self.is_dev_version:
                    raise
                LOGGER.critical(e)
                copyfile(json_file, '{}.back'.format(json_file))
                message = tr(
                    'Errors encountered while reading the last layer tree state. '
                    'Please re-configure the options in the Layers tab completely. '
                    'The previous .cfg has been saved as .cfg.back')
                QMessageBox.critical(
                    self.dlg, tr('Lizmap Error'), message, QMessageBox.Ok)
                self.log(message, abort=True, textarea=self.dlg.outLog)
                LOGGER.critical('Error while reading the CFG file')

        else:
            LOGGER.info('Lizmap CFG does not exist for this project.')
            for key in self.layers_table.keys():
                manager = self.layers_table[key].get('manager')
                if manager:
                    manager.truncate()

        # Set the global options (map, tools, etc.)
        for key, item in self.global_options.items():
            if item.get('widget'):
                if item.get('tooltip'):
                    item['widget'].setToolTip(item.get('tooltip'))

                if item['wType'] == 'checkbox':
                    item['widget'].setChecked(item['default'])
                    if key in json_options:
                        item['widget'].setChecked(to_bool(json_options[key]))

                if item['wType'] in ('text', 'textarea'):
                    if isinstance(item['default'], (list, tuple)):
                        item['widget'].setText(", ".join(map(str, item['default'])))
                    else:
                        item['widget'].setText(str(item['default']))
                    if key in json_options:
                        if isinstance(json_options[key], (list, tuple)):
                            item['widget'].setText(", ".join(map(str, json_options[key])))
                        else:
                            item['widget'].setText(str(json_options[key]))

                if item['wType'] == 'wysiwyg':
                    item['widget'].set_html_content(str(item['default']))

                # if item['wType'] in ('html'):
                # if isinstance(item['default'], (list, tuple)):
                # item['widget'].setHtml(", ".join(map(str, item['default'])))
                # else:
                # item['widget'].setHtml(str(item['default']))
                # if jsonOptions.has_key(key):
                # if isinstance(jsonOptions[key], (list, tuple)):
                # item['widget'].setHtml(", ".join(map(str, jsonOptions[key])))
                # else:
                # item['widget'].setHtml(str(jsonOptions[key]))

                if item['wType'] == 'spinbox':
                    item['widget'].setValue(int(item['default']))
                    if key in json_options:
                        item['widget'].setValue(int(json_options[key]))

                if item['wType'] == 'list':
                    if isinstance(item['list'][0], (list, tuple)):
                        # New way with icon, tooltip, translated label
                        pass
                    else:
                        # Legacy way
                        for i, item_config in enumerate(item['list']):
                            item['widget'].setItemData(i, item_config)

                        if item['default'] in item['list']:
                            index = item['widget'].findData(item['default'])
                            item['widget'].setCurrentIndex(index)

                    if key in json_options:
                        index = item['widget'].findData(json_options[key])
                        if index:
                            item['widget'].setCurrentIndex(index)

        # Set layer combobox
        for key, item in self.global_options.items():
            if item.get('widget'):
                if item['wType'] == 'layers':
                    if key in json_options:
                        for lyr in self.project.mapLayers().values():
                            if lyr.id() == json_options[key]:
                                item['widget'].setLayer(lyr)
                                break

        # Then set field combobox
        for key, item in self.global_options.items():
            if item.get('widget'):
                if item['wType'] == 'fields':
                    if key in json_options:
                        item['widget'].setField(str(json_options[key]))

        # Fill the table widgets
        for key, item in self.layers_table.items():
            if skip_tables:
                continue
            self.load_config_into_table_widget(key)

        self.dlg.check_ign_french_free_key()
        out = '' if json_file.exists() else 'out'
        LOGGER.info(f'Dialog has been loaded successful, with{out} CFG file')

        if self.project.fileName().lower().endswith('qgs'):
            # Manage lizmap_user project variable
            variables = self.project.customVariables()
            if 'lizmap_user' in variables.keys() and not self.dlg.check_cfg_file_exists() and not skip_tables:
                # The variable 'lizmap_user' exists in the project as a variable
                # But no CFG was found, maybe the project has been renamed.
                message = tr(
                    'We have detected that this QGIS project has been used before with the Lizmap plugin (due to the '
                    'variable "lizmap_user" in your project properties dialog).'
                )
                message += '\n\n'
                message += tr(
                    "However, we couldn't detect the Lizmap configuration file '{}' anymore. A new "
                    "configuration from scratch is used."
                ).format(self.dlg.cfg_file())
                message += '\n\n'
                message += tr(
                    'Did you rename this QGIS project file ? If you want to keep your previous configuration, you '
                    'should find your previous Lizmap CFG file and use the path above. Lizmap will load it.'
                )
                QMessageBox.warning(
                    self.dlg, tr('New Lizmap configuration'), message, QMessageBox.Ok)

            # Add default variables in the project
            if not variables.get('lizmap_user'):
                variables['lizmap_user'] = ''
            if not variables.get('lizmap_user_groups'):
                variables['lizmap_user_groups'] = list()
            self.project.setCustomVariables(variables)

        self.layerList = dict()

        # Get embedded groups
        self.embeddedGroups = None

        # Fill the layer tree
        data = self.populate_layer_tree()

        # Fill base-layer startup
        self.on_baselayer_checkbox_change()
        self.set_startup_baselayer_from_config()

        # The return is used in tests
        return data

    def load_config_into_table_widget(self, key):
        """Load data from lizmap config file into the widget.

        :param key: The key section to load according to the table.
        :type key: basestring
        """
        # Get parameters for the widget
        lt = self.layers_table[key]

        if lt.get('manager'):
            # Note, new generation form/manager do not use this function
            return

        widget = lt['tableWidget']
        attributes = lt['cols']
        json_config = lt['jsonConfig']

        # Get index of layerId column
        store_layer_id = 'layerId' in lt['cols']

        # Fill edition layers capabilities
        if key == 'editionLayers' and json_config:
            for k, v in json_config.items():
                if 'capabilities' in v:
                    for x, y in v['capabilities'].items():
                        json_config[k][x] = y

        # empty previous content
        for row in range(widget.rowCount()):
            widget.removeRow(row)
        widget.setRowCount(0)

        # fill from the json if exists
        col_count = len(attributes)

        # +1 for layer name column (1st column)
        if store_layer_id:
            col_count += 1

        if json_config:
            # reorder data if needed
            if 'order' in list(json_config.items())[0][1]:
                # FIXME shadow error with key
                data = [(k, json_config[k]) for k in sorted(json_config, key=lambda key: json_config[key]['order'])]
            else:
                data = list(json_config.items())

            # load content from json file
            project_layers_ids = list(self.project.mapLayers().keys())
            for k, v in data:
                # check if the layer still exists in the QGIS project
                if 'layerId' in list(v.keys()):
                    if v['layerId'] not in project_layers_ids:
                        continue
                tw_row_count = widget.rowCount()
                # add a new line
                widget.setRowCount(tw_row_count + 1)
                widget.setColumnCount(col_count)
                i = 0
                if store_layer_id:
                    # add layer name column - get name from layer if possible (if the user has renamed the layer)
                    icon = None
                    if 'layerId' in list(v.keys()):
                        layer = self.project.mapLayer(v['layerId'])
                        if layer:
                            k = layer.name()
                            # noinspection PyArgumentList
                            icon = QgsMapLayerModel.iconForLayer(layer)

                    new_item = QTableWidgetItem(k)
                    if icon:
                        new_item.setIcon(icon)
                    widget.setItem(tw_row_count, 0, new_item)
                    i += 1
                # other information
                for key in attributes:
                    if key in v:
                        value = v[key]
                    else:
                        value = ''
                    new_item = QTableWidgetItem(str(value))
                    widget.setItem(tw_row_count, i, new_item)
                    i += 1

        if key == 'lizmapExternalBaselayers':
            # We enable this widget only if there is at least one existing entry in the CFG. #121
            rows = widget.rowCount()
            if rows >= 1:
                self.dlg.gb_lizmapExternalBaselayers.setVisible(True)
                LOGGER.warning('Table "lizmapExternalBaselayers" has been loaded, which is deprecated')
        else:
            LOGGER.info('Table "{}" has been loaded'.format(key))

    def get_qgis_layer_by_id(self, my_id) -> Optional[QgsMapLayer]:
        """Get a QgsLayer by its ID"""
        for layer in self.project.mapLayers().values():
            if my_id == layer.id():
                return layer
        return None

    def set_initial_extent_from_project(self):
        """
        Get the project WMS advertised extent
        and set the initial x minimum, y minimum, x maximum, y maximum
        in the map options tab
        """
        p_wms_extent = self.project.readListEntry('WMSExtent', '')[0]
        if len(p_wms_extent) > 1:
            extent = '{}, {}, {}, {}'.format(
                p_wms_extent[0],
                p_wms_extent[1],
                p_wms_extent[2],
                p_wms_extent[3]
            )
            self.dlg.inInitialExtent.setText(extent)

        LOGGER.info('Setting extent from the project')

    def set_initial_extent_from_canvas(self):
        """
        Get the map canvas extent
        and set the initial x minimum, y minimum, x maximum, y maximum
        in the map options tab
        """
        # Get map canvas extent
        extent = self.iface.mapCanvas().extent()
        initial_extent = '{}, {}, {}, {}'.format(
            extent.xMinimum(),
            extent.yMinimum(),
            extent.xMaximum(),
            extent.yMaximum()
        )
        self.dlg.inInitialExtent.setText(initial_extent)
        LOGGER.info('Setting extent from the canvas')

    def remove_selected_layer_from_table(self, key):
        """
        Remove a layer from the list of layers
        for which to have the "locate by layer" tool
        """
        tw = self.layers_table[key]['tableWidget']
        tw.removeRow(tw.currentRow())
        LOGGER.info('Removing one row in table "{}"'.format(key))

    def remove_layer_from_table_by_layer_ids(self, layer_ids):
        """
        Remove layers from tables when deleted from layer registry
        """
        if not self.dlg.check_cfg_file_exists():
            return

        for key, item in self.layers_table.items():

            manager = self.layers_table[key].get('manager')
            if manager:
                manager.layers_has_been_deleted(layer_ids)
                continue

            tw = self.layers_table[key]['tableWidget']

            # Count lines
            tw_row_count = tw.rowCount()
            if not tw_row_count:
                continue

            # Get index of layerId column
            if 'layerId' not in self.layers_table[key]['cols']:
                continue
            idx = self.layers_table[key]['cols'].index('layerId') + 1

            # Remove layer if layerId match
            for row in range(tw_row_count):
                if tw.item(row, idx):
                    item_layer_id = str(tw.item(row, idx).text())
                    if item_layer_id in layer_ids:
                        tw.removeRow(row)

        LOGGER.info('Layer ID "{}" has been removed from the project'.format(layer_ids))

    def layout_renamed(self, layout, new_name: str):
        """ When a layout has been renamed in the project. """
        if not self.dlg.check_cfg_file_exists():
            return

        self.layers_table['layouts']['manager'].layout_renamed(layout, new_name)

    def layout_removed(self, name: str):
        """ When a layout has been removed from the project. """
        if not self.dlg.check_cfg_file_exists():
            return

        self.layers_table['layouts']['manager'].layout_removed(name)

    def check_wfs_is_checked(self, layer):
        wfs_layers_list = self.project.readListEntry('WFSLayers', '')[0]
        has_wfs_option = False
        for wfs_layer in wfs_layers_list:
            if layer.id() == wfs_layer:
                has_wfs_option = True
        if not has_wfs_option:
            self.display_error(tr(
                'The layers you have chosen for this tool must be checked in the "WFS Capabilities" option of the '
                'QGIS Server tab in the "Project Properties" dialog.'))
            return False
        return True

    def display_error(self, message):
        QMessageBox.critical(
            self.dlg,
            tr('Lizmap Error'),
            message,
            QMessageBox.Ok)

    # noinspection PyPep8Naming
    def addLayerToLizmapBaselayers(self):
        """Add a layer in the list of Lizmap external baselayers.

        This is a deprecated feature in lizmap.
        Users should use embedded layer instead.

        THIS WILL BE REMOVED IN LIZMAP 4.
        """
        layerRepository = str(self.dlg.inLizmapBaselayerRepository.text()).strip(' \t')
        layerProject = str(self.dlg.inLizmapBaselayerProject.text()).strip(' \t')
        layerName = str(self.dlg.inLizmapBaselayerLayer.text()).strip(' \t')
        layerTitle = str(self.dlg.inLizmapBaselayerTitle.text()).strip(' \t')
        layerImageFormat = str(self.dlg.inLizmapBaselayerImageFormat.text()).strip(' \t')
        content = [layerRepository, layerProject, layerName, layerTitle, layerImageFormat]
        # Check that every option is set
        for val in content:
            if not val:
                QMessageBox.critical(
                    self.dlg,
                    tr("Lizmap Error"),
                    tr(
                        "Please check that all input fields have been filled: repository, project, "
                        "layer name and title"),
                    QMessageBox.Ok
                )
                return

        lblTableWidget = self.dlg.twLizmapBaselayers
        twRowCount = lblTableWidget.rowCount()
        content.append(twRowCount)  # store order
        colCount = len(content)
        if twRowCount < 6:
            # set new rowCount
            lblTableWidget.setRowCount(twRowCount + 1)
            lblTableWidget.setColumnCount(colCount)
            # Add content in the widget line
            i = 0
            for val in content:
                item = QTableWidgetItem(val)
                lblTableWidget.setItem(twRowCount, i, item)
                i += 1

            LOGGER.info('Layer has been added to the base layer list')

    def set_tree_item_data(self, item_type, item_key, json_layers):
        """Define default data or data from previous configuration for one item (layer or group)
        Used in the method populateLayerTree
        """
        # Type : group or layer
        self.myDic[item_key]['type'] = item_type

        # DEFAULT VALUES : generic default values for layers and group
        self.myDic[item_key]['name'] = item_key
        for key, item in self.layer_options_list.items():
            self.myDic[item_key][key] = item['default']
        self.myDic[item_key]['title'] = self.myDic[item_key]['name']

        if item_type == 'group':
            # embedded group ?
            if self.embeddedGroups and item_key in self.embeddedGroups:
                p_name = self.embeddedGroups[item_key]['project']
                p_name = os.path.splitext(os.path.basename(p_name))[0]
                self.myDic[item_key]['sourceProject'] = p_name

        # DEFAULT VALUES : layers have got more precise data
        keep_metadata = False
        if item_type == 'layer':

            # layer name
            layer = self.get_qgis_layer_by_id(item_key)
            self.myDic[item_key]['name'] = layer.name()
            # title and abstract
            self.myDic[item_key]['title'] = layer.name()
            if layer.title():
                self.myDic[item_key]['title'] = layer.title()
                keep_metadata = True
            if layer.abstract():
                self.myDic[item_key]['abstract'] = layer.abstract()
                keep_metadata = True

            if not self.myDic[item_key]['link']:
                self.myDic[item_key]['link'] = layer_property(layer, LayerProperties.DataUrl)

            # hide non geo layers (csv, etc.)
            # if layer.type() == 0:
            #    if layer.geometryType() == 4:
            #        self.l display = False

            # layer scale visibility
            if layer.hasScaleBasedVisibility():
                self.myDic[item_key]['minScale'] = layer.maximumScale()
                self.myDic[item_key]['maxScale'] = layer.minimumScale()
            # toggled : check if layer is toggled in qgis legend
            # self.myDic[itemKey]['toggled'] = layer.self.iface.legendInterface().isLayerVisible(layer)
            self.myDic[item_key]['toggled'] = False
            # group as layer : always False obviously because it is already a layer
            self.myDic[item_key]['groupAsLayer'] = False
            # embedded layer ?
            from_project = self.project.layerIsEmbedded(item_key)
            if os.path.exists(from_project):
                p_name = os.path.splitext(os.path.basename(from_project))[0]
                self.myDic[item_key]['sourceProject'] = p_name

        # OVERRIDE DEFAULT FROM CONFIGURATION FILE
        if self.myDic[item_key]['name'] in json_layers:
            json_key = self.myDic[item_key]['name']
            LOGGER.info('Reading configuration from dictionary for layer {}'.format(json_key))
            # loop through layer options to override
            for key, item in self.layer_options_list.items():
                # override only for ui widgets
                if item.get('widget'):
                    if key in json_layers[json_key]:

                        if key == 'legend_image_option' and 'noLegendImage' in json_layers[json_key]:
                            if self.myDic[item_key].get('legend_image_option'):
                                # The key is already set before with noLegendImage
                                LOGGER.info(
                                    "Skip key legend_image_option because it has been set previously with noLegendImage"
                                )
                                continue

                        # checkboxes
                        if item['wType'] == 'checkbox':
                            self.myDic[item_key][key] = to_bool(json_layers[json_key][key], False)
                        # spin box
                        elif item['wType'] == 'spinbox':
                            if json_layers[json_key][key] != '':
                                self.myDic[item_key][key] = json_layers[json_key][key]
                        # text inputs
                        elif item['wType'] in ('text', 'textarea'):
                            if json_layers[json_key][key] != '':
                                if item.get('isMetadata'):  # title and abstract
                                    if not keep_metadata:
                                        self.myDic[item_key][key] = json_layers[json_key][key]
                                else:
                                    self.myDic[item_key][key] = json_layers[json_key][key]
                        # lists
                        elif item['wType'] == 'list':
                            # New way with data, label, tooltip and icon
                            datas = [j[0] for j in item['list']]
                            if json_layers[json_key][key] in datas:
                                self.myDic[item_key][key] = json_layers[json_key][key]

                else:
                    if key == 'noLegendImage' and 'noLegendImage' in json_layers.get(json_key):
                        tmp = 'hide_at_startup'  # Default value
                        if to_bool(json_layers[json_key].get('noLegendImage')):
                            tmp = 'disabled'
                        self.myDic[item_key]['legend_image_option'] = tmp

                    # LOGGER.info('Skip key {} because no UI widget'.format(key))

                # popupContent
                if key == 'popupTemplate':
                    if key in json_layers[json_key]:
                        self.myDic[item_key][key] = json_layers[json_key][key]

    def process_node(self, node, parent_node, json_layers):
        """
        Process a single node of the QGIS layer tree and adds it to Lizmap layer tree.
        """
        for child in node.children():
            if QgsLayerTree.isGroup(child):
                if not isinstance(child, QgsLayerTreeGroup):
                    # Sip cast issue
                    # https://github.com/3liz/lizmap-plugin/issues/299
                    # noinspection PyTypeChecker
                    child = sip.cast(child, QgsLayerTreeGroup)
                child_id = child.name()
                child_type = 'group'
                # noinspection PyCallByClass,PyArgumentList
                child_icon = QIcon(QgsApplication.iconPath('mActionFolder.svg'))
            elif QgsLayerTree.isLayer(child):
                if not isinstance(child, QgsLayerTreeLayer):
                    # Sip cast issue
                    # https://github.com/3liz/lizmap-plugin/issues/299
                    # noinspection PyTypeChecker
                    child = sip.cast(child, QgsLayerTreeLayer)
                child_id = child.layerId()
                child_type = 'layer'
                # noinspection PyArgumentList
                child_icon = QgsMapLayerModel.iconForLayer(child.layer())
            else:
                raise Exception('Unknown child type')

            # Select an existing item, select the header item or create the item
            if child_id in self.myDic:
                # If the item already exists in self.myDic, select it
                item = self.myDic[child_id]['item']
            elif child_id == '':
                # If the id is empty string, this is a root layer, select the headerItem
                item = self.dlg.layer_tree.headerItem()
            else:
                # else create the item and add it to the header item
                # add the item to the dictionary
                self.myDic[child_id] = {'id': child_id}
                if child_type == 'group':
                    # it is a group
                    self.set_tree_item_data('group', child_id, json_layers)
                else:
                    # it is a layer
                    self.set_tree_item_data('layer', child_id, json_layers)

                item = QTreeWidgetItem(
                    [
                        str(self.myDic[child_id]['name']),
                        str(self.myDic[child_id]['id']),
                        self.myDic[child_id]['type']
                    ]
                )
                item.setToolTip(0, self.myDic[child_id]['name'])
                item.setIcon(0, child_icon)
                self.myDic[child_id]['item'] = item

                # Move group or layer to its parent node
                if not parent_node:
                    self.dlg.layer_tree.addTopLevelItem(item)
                else:
                    parent_node.addChild(item)

            if child_type == 'group':
                self.process_node(child, item, json_layers)

    def read_lizmap_config_file(self) -> dict:
        """ Read the CFG file and returns the JSON content. """
        if not self.dlg.check_cfg_file_exists():
            return {}

        with open(self.dlg.cfg_file(), encoding='utf8') as f:
            json_file_reader = f.read()

        # noinspection PyBroadException
        try:
            sjson = json.loads(json_file_reader)
            return sjson['layers']
        except Exception:
            if self.is_dev_version:
                raise
            message = tr(
                'Errors encountered while reading the last layer tree state. '
                'Please re-configure the options in the Layers tab completely'
            )
            QMessageBox.critical(self.dlg, tr('Lizmap Error'), '', QMessageBox.Ok)
            self.log(message, abort=True, textarea=self.dlg.outLog)
            return {}

    def populate_layer_tree(self) -> dict:
        """Populate the layer tree of the Layers tab from QGIS legend interface.

        Needs to be refactored.
        """
        self.dlg.block_signals_address(True)

        self.dlg.layer_tree.clear()
        self.myDic = {}

        json_layers = self.read_lizmap_config_file()
        root = self.project.layerTreeRoot()

        # Recursively process layer tree nodes
        self.process_node(root, None, json_layers)
        self.dlg.layer_tree.expandAll()

        # Add the self.myDic to the global layerList dictionary
        self.layerList = self.myDic

        self.dlg.block_signals_address(False)
        self.enable_check_box(False)

        # The return is used in tests
        return json_layers

    def from_data_to_ui_for_layer_group(self):
        """ Restore layer/group values into each field when selecting a layer in the tree. """
        # get the selected item
        item = self.dlg.layer_tree.currentItem()
        if item:
            self.enable_check_box(True)
        else:
            self.enable_check_box(False)

        i_key = item.text(1)
        if i_key in self.layerList:
            # get information about the layer or the group from the layerList dictionary
            selected_item = self.layerList[i_key]

            # set options
            for key, val in self.layer_options_list.items():
                if val.get('widget'):

                    if val.get('tooltip'):
                        val['widget'].setToolTip(val.get('tooltip'))

                    if val['wType'] in ('text', 'textarea'):
                        if val['type'] == 'list':
                            data = selected_item[key]
                            if isinstance(data, str):
                                # It should be a list, but it has been temporary a string during the dev process
                                data = [data]
                            text = ','.join(data)
                        else:
                            text = selected_item[key]
                        if val['wType'] == 'text':
                            val['widget'].setText(text)
                        else:
                            # Abstract is the only textarea
                            val['widget'].setPlainText(text)
                    elif val['wType'] == 'spinbox':
                        val['widget'].setValue(int(selected_item[key]))
                    elif val['wType'] == 'checkbox':
                        val['widget'].setChecked(selected_item[key])
                        children = val.get('children')
                        if children:
                            exclusive = val.get('exclusive', False)
                            if exclusive:
                                is_enabled = not selected_item[key]
                            else:
                                is_enabled = selected_item[key]
                            self.layer_options_list[children]['widget'].setEnabled(is_enabled)
                            if self.layer_options_list[children]['wType'] == 'checkbox' and not is_enabled:
                                if self.layer_options_list[children]['widget'].isChecked():
                                    self.layer_options_list[children]['widget'].setChecked(False)

                    elif val['wType'] == 'list':
                        # New way with data, label, tooltip and icon
                        index = val['widget'].findData(selected_item[key])

                        if index < 0 and val.get('default'):
                            # Get back to default
                            index = val['widget'].findData(val['default'])

                        val['widget'].setCurrentIndex(index)

                    # deactivate wms checkbox if not needed
                    if key == 'externalWmsToggle':
                        wms_enabled = self.get_item_wms_capability(selected_item)
                        if wms_enabled is not None:
                            self.dlg.cbExternalWms.setEnabled(wms_enabled)
                            if wms_enabled:
                                self.dlg.cbExternalWms.toggled.connect(self.external_wms_toggled)
                                self.external_wms_toggled()
                            else:
                                self.dlg.cbExternalWms.setChecked(False)
                                try:
                                    self.dlg.cbExternalWms.toggled.disconnect(self.external_wms_toggled)
                                except TypeError:
                                    # The object was not connected
                                    pass

            layer = self._current_selected_layer()  # It can be a layer or a group

            # Disable popup configuration for groups and raster
            # Disable QGIS popup for layer without geom
            is_vector = isinstance(layer, QgsVectorLayer)
            # noinspection PyUnresolvedReferences
            has_geom = is_vector and layer.wkbType() != QgsWkbTypes.NoGeometry
            self.dlg.btConfigurePopup.setEnabled(has_geom)
            self.dlg.btQgisPopupFromForm.setEnabled(is_vector)
            self.dlg.button_generate_html_table.setEnabled(is_vector)
            self.layer_options_list['popupSource']['widget'].setEnabled(is_vector)

            # For a group, there isn't the toggle option, #298, TEMPORARY DISABLED
            tooltip = tr("If the layer is displayed by default")
            self.layer_options_list['toggled']['widget'].setToolTip(tooltip)
            # try:
            #     # We always disconnect everything
            #     self.layer_options_list['groupAsLayer']['widget'].disconnect()
            # except TypeError:
            #     pass
            #
            # if isinstance(layer, QgsMapLayer):
            #     # Always enabled
            #     self.layer_options_list['toggled']['widget'].setEnabled(True)
            #     tooltip = tr("If the layer is displayed by default")
            #     self.layer_options_list['toggled']['widget'].setToolTip(tooltip)
            # else:
            #     # It depends on the "Group as layer" checked or not, so it has a signal
            #     self.layer_options_list['groupAsLayer']['widget'].stateChanged.connect(
            #         self.enable_or_not_toggle_checkbox)
            #     self.enable_or_not_toggle_checkbox()

            # Checkbox display children features
            self.dlg.relation_stacked_widget.setCurrentWidget(self.dlg.page_no_relation)
            if is_vector:
                if len(self.project.relationManager().referencedRelations(layer)) >= 1:
                    # We display options
                    self.dlg.relation_stacked_widget.setCurrentWidget(self.dlg.page_display_relation)

        else:
            # set default values for this layer/group
            for key, val in self.layer_options_list.items():
                if val.get('widget'):
                    if val['wType'] in ('text', 'textarea'):
                        if isinstance(val['default'], (list, tuple)):
                            text = ','.join(val['default'])
                        else:
                            text = val['default']
                        if val['wType'] == 'text':
                            val['widget'].setText(text)
                        else:
                            # Abstract is the only textarea for now
                            # We shouldn't have any default value, but let's support it
                            val['widget'].setPlainText(text)
                    elif val['wType'] == 'spinbox':
                        val['widget'].setValue(val['default'])
                    elif val['wType'] == 'checkbox':
                        val['widget'].setChecked(val['default'])
                    elif val['wType'] == 'list':

                        # New way with data, label, tooltip and icon
                        index = val['widget'].findData(val['default'])
                        val['widget'].setCurrentIndex(index)

        self.enable_popup_source_button()

    # def enable_or_not_toggle_checkbox(self):
    #     """ Only for groups, to determine the state of the "toggled" option. """
    #     if self.layer_options_list['groupAsLayer']['widget'].isChecked():
    #         self.layer_options_list['toggled']['widget'].setEnabled(True)
    #         tooltip = tr(
    #             "All layers in this group are considered as a unique layer. This new layer can be displayed "
    #             "or not.")
    #     else:
    #         self.layer_options_list['toggled']['widget'].setEnabled(False)
    #         self.layer_options_list['toggled']['widget'].setChecked(False)
    #         tooltip = tr("For a group, it depends on layers inside the group")
    #     self.layer_options_list['toggled']['widget'].setToolTip(tooltip)

    def external_wms_toggled(self):
        """ Disable the format combobox is the checkbox third party WMS is checked. """
        self.dlg.liImageFormat.setEnabled(not self.dlg.cbExternalWms.isChecked())

    def get_item_wms_capability(self, selected_item) -> Optional[bool]:
        """
        Check if an item in the tree is a layer
        and if it is a WMS layer
        """
        wms_enabled = False
        is_layer = selected_item['type'] == 'layer'
        if is_layer:
            layer = self.get_qgis_layer_by_id(selected_item['id'])
            if not layer:
                return
            if layer.providerType() in ['wms']:
                if get_layer_wms_parameters(layer):
                    wms_enabled = True
        return wms_enabled

    @staticmethod
    def string_to_list(text):
        """ Format a string to a list. """
        data = text.split(',') if len(text) > 0 else []
        data = [item.strip() for item in data]
        return data

    def save_value_layer_group_data(self, key: str):
        """ Save the new value from the UI in the global layer property self.layerList.

        Function called the corresponding UI widget has sent changed signal.
        """
        key = str(key)
        # get the selected item in the layer tree
        item = self.dlg.layer_tree.currentItem()
        # get the definition for this property
        layer_option = self.layer_options_list[key]
        # modify the property for the selected item
        if item and item.text(1) in self.layerList:
            if layer_option['wType'] == 'text':
                text = layer_option['widget'].text()
                if layer_option['type'] == 'list':
                    text = self.string_to_list(text)
                self.layerList[item.text(1)][key] = text
                self.set_layer_metadata(item, key)
            elif layer_option['wType'] == 'textarea':
                self.layerList[item.text(1)][key] = layer_option['widget'].toPlainText()
                self.set_layer_metadata(item, key)
            elif layer_option['wType'] == 'spinbox':
                self.layerList[item.text(1)][key] = layer_option['widget'].value()
            elif layer_option['wType'] == 'checkbox':
                checked = layer_option['widget'].isChecked()
                self.layerList[item.text(1)][key] = checked
                children = layer_option.get('children')
                if children:
                    exclusive = layer_option.get('exclusive', False)
                    if exclusive:
                        is_enabled = not checked
                    else:
                        is_enabled = checked
                    self.layer_options_list[children]['widget'].setEnabled(is_enabled)
                    if self.layer_options_list[children]['wType'] == 'checkbox' and not is_enabled:
                        if self.layer_options_list[children]['widget'].isChecked():
                            self.layer_options_list[children]['widget'].setChecked(False)
            elif layer_option['wType'] == 'list':
                # New way with data, label, tooltip and icon
                datas = [j[0] for j in layer_option['list']]
                self.layerList[item.text(1)][key] = datas[layer_option['widget'].currentIndex()]

            # Deactivate the "exclude" widget if necessary
            if 'exclude' in layer_option \
                    and layer_option['wType'] == 'checkbox' \
                    and layer_option['widget'].isChecked() \
                    and layer_option['exclude']['widget'].isChecked():
                layer_option['exclude']['widget'].setChecked(False)
                self.layerList[item.text(1)][layer_option['exclude']['key']] = False

    def set_layer_metadata(self, item, key):
        """Set the title/abstract/link QGIS metadata when corresponding item is changed
        Used in setLayerProperty"""
        if 'isMetadata' in self.layer_options_list[key]:
            # modify the layer.title|abstract|link() if possible
            if self.layerList[item.text(1)]['type'] == 'layer':
                layer = self.get_qgis_layer_by_id(item.text(1))
                if isinstance(layer, QgsMapLayer):
                    if key == 'title':
                        layer.setTitle(self.layerList[item.text(1)][key])
                    if key == 'abstract':
                        layer.setAbstract(self.layerList[item.text(1)][key])

    def convert_html_maptip(self):
        """ Trying to convert a Lizmap popup to HTML popup. """
        item = self.dlg.layer_tree.currentItem()
        if item and item.text(1) in self.layerList:
            if 'popupTemplate' in self.layerList[item.text(1)]:
                self.layerList[item.text(1)]['popup'] = True
                text = self.layerList[item.text(1)]['popupTemplate']

                layer = self._current_selected_layer()
                html, errors = convert_lizmap_popup(text, layer)
                if errors:
                    QMessageBox.warning(
                        self.dlg,
                        tr('Lizmap - Warning'),
                        tr(
                            'Some fields or alias could not be found in the layer. You must check the result manually '
                            'about these values below :'
                        ) + '<br><br>' + ','.join(errors),
                        QMessageBox.Ok)

                flag = self._set_maptip(layer, html)
                if flag:
                    index = self.layer_options_list['popupSource']['widget'].findData('qgis')
                    self.layer_options_list['popupSource']['widget'].setCurrentIndex(index)

    def configure_html_popup(self):
        """Open the dialog with a text field to store the popup template for one layer/group"""
        # get the selected item in the layer tree
        item = self.dlg.layer_tree.currentItem()
        if not item:
            return

        if not item.text(1) in self.layerList:
            return

        # do nothing if no popup configured for this layer/group
        if not to_bool(self.layerList[item.text(1)]['popup']):
            return

        # Set the content of the QTextEdit if needed
        if 'popupTemplate' in self.layerList[item.text(1)]:
            self.layerList[item.text(1)]['popup'] = True
            text = self.layerList[item.text(1)]['popupTemplate']
        else:
            text = ''

        LOGGER.info('Opening the popup configuration')

        layer = self._current_selected_layer()
        data = self.layer_options_list['popupSource']['widget'].currentData()
        if data == 'lizmap':
            # Legacy
            # Lizmap HTML popup
            if isinstance(layer, QgsVectorLayer):
                LOGGER.warning("The 'lizmap' popup is deprecated for vector layer. This will be removed soon.")

            popup_dialog = LizmapPopupDialog(text)
            if not popup_dialog.exec_():
                return

            content = popup_dialog.txtPopup.text()

            # Get the selected item in the layer tree
            item = self.dlg.layer_tree.currentItem()
            if item and item.text(1) in self.layerList:
                # Write the content into the global object
                self.layerList[item.text(1)]['popupTemplate'] = content
                if isinstance(layer, QgsVectorLayer):
                    LOGGER.warning("The 'lizmap' popup is deprecated for vector layer. This will be removed soon.")

        else:
            # QGIS HTML maptip
            layer: QgsVectorLayer
            html_editor = HtmlEditorDialog()
            html_editor.set_layer(layer)
            html_editor.editor.set_html_content(layer.mapTipTemplate())
            if not html_editor.exec_():
                return

            self._set_maptip(layer, html_editor.editor.html_content(), False)

    def _current_selected_layer(self) -> Optional[QgsMapLayer]:
        """ Current selected map layer in the tree. """
        item = self.dlg.layer_tree.currentItem()
        if item and item.text(1) in self.layerList:
            lid = item.text(1)
            layers = [layer for layer in self.project.mapLayers().values() if layer.id() == lid]
            if not layers:
                LOGGER.warning('Layers not found with searched text from the tree : {}'.format(lid))
                return
        else:
            LOGGER.warning('No item selected in the Lizmap layer tree.')
            return

        layer = layers[0]
        return layer

    def link_from_properties(self):
        """ Button set link from layer in the Lizmap configuration. """
        layer = self._current_selected_layer()
        value = layer_property(layer, LayerProperties.DataUrl)
        self.layer_options_list['link']['widget'].setText(value)

    def _set_maptip(self, layer: QgsVectorLayer, html_content: str, check: bool = True) -> bool:
        """ Internal function to set the maptip on a layer. """
        if check and layer.mapTipTemplate() != '':
            box = QMessageBox(self.dlg)
            box.setIcon(QMessageBox.Question)
            box.setWindowIcon(QIcon(resources_path('icons', 'icon.png')),)
            box.setWindowTitle(tr('Existing maptip for layer {}').format(layer.title()))
            box.setText(tr(
                'A maptip already exists for this layer. This is going to override it. '
                'Are you sure you want to continue ?'))
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            result = box.exec_()
            if result == QMessageBox.No:
                return False

        layer.setMapTipTemplate(html_content)
        QMessageBox.information(
            self.dlg,
            tr('Maptip'),
            tr('The maptip has been set in the layer "{}".').format(layer.name()),
            QMessageBox.Ok
        )
        return True

    def html_table_from_layer(self):
        """ Button set popup maptip from layer in the Lizmap configuration. """
        layer = self._current_selected_layer()
        if not isinstance(layer, QgsVectorLayer):
            return

        table_template = """<table class="table table-condensed table-striped table-bordered lizmapPopupTable">
  <thead>
    <tr>
      <th>{field}</th>
      <th>{value}</th>
    </tr>
  </thead>
  <tbody>
{fields_template}
  </tbody>
</table>"""
        field_template = """    <tr>
      <th>{name}</th>
      <td>[% "{value}" %]</td>
    </tr>
"""
        fields = ""
        for field in layer.fields():
            fields += field_template.format(name=field.displayName(), value=field.name())
        result = table_template.format(field=tr("Field"), value=tr("Value"), fields_template=fields)
        self._set_maptip(layer, result)

    def maptip_from_form(self):
        """ Button set popup maptip from DND form in the Lizmap configuration. """
        layer = self._current_selected_layer()
        if not isinstance(layer, QgsVectorLayer):
            return

        config = layer.editFormConfig()
        # noinspection PyUnresolvedReferences
        if config.layout() != QgsEditFormConfig.TabLayout:
            LOGGER.warning('Maptip : the layer is not using a drag and drop form.')
            QMessageBox.warning(
                self.dlg,
                tr('Lizmap - Warning'),
                tr('The form for this layer is not a drag and drop layout.'),
                QMessageBox.Ok)
            return

        root = config.invisibleRootContainer()
        relation_manager = self.project.relationManager()
        html_content = Tooltip.create_popup_node_item_from_form(layer, root, 0, [], '', relation_manager)
        html_content = Tooltip.create_popup(html_content)
        html_content += Tooltip.css()
        self._set_maptip(layer, html_content)

    def write_project_config_file(self, lwc_version: LwcVersions, with_gui: bool = True):
        """ Write a Lizmap configuration to the file. """
        liz2json = self.project_config_file(lwc_version, with_gui)
        json_file_content = json.dumps(
            liz2json,
            sort_keys=False,
            indent=4
        )
        json_file_content += '\n'

        # Get the project data
        json_file = self.dlg.cfg_file()
        with open(json_file, 'w', encoding='utf8') as cfg_file:
            cfg_file.write(json_file_content)

        LOGGER.info('The CFG file has been written to "{}"'.format(json_file))
        self.clean_project()

    def project_config_file(self, lwc_version: LwcVersions, with_gui: bool = True, check_server=True) -> Dict:
        """ Generate the CFG file with all options. """
        valid, _ = self.check_project_validity()

        LOGGER.info("Writing CFG file for LWC version {}".format(lwc_version.value))
        current_version = self.global_options['metadata']['lizmap_plugin_version']['default']
        if self.is_dev_version:
            next_version = next_git_tag()
            if next_version != 'next':
                current_version = next_version

        warnings = []

        # Layer ID as short name
        if lwc_version >= LwcVersions.Lizmap_3_6:
            use_layer_id, _ = self.project.readEntry('WMSUseLayerIDs', '/')
            if to_bool(use_layer_id, False):
                QMessageBox.warning(
                    self.dlg,
                    tr('Use layer IDs as name'),
                    '{}\n\n{}'.format(
                        tr(
                            "Since Lizmap Web Client 3.6, it's not possible anymore to use the option 'Use layer IDs "
                            "as name' in the project properties dialog, QGIS server tab, then WMS capabilities."
                        ),
                        tr("Please uncheck this checkbox and re-save the Lizmap configuration file.")
                    ),
                    QMessageBox.Ok
                )
                warnings.append(Warnings.UseLayerIdAsName.value)

        target_status = self.dlg.server_combo.currentData(ServerComboData.LwcBranchStatus.value)
        if not target_status:
            target_status = ReleaseStatus.Unknown

        server_metadata = self.dlg.server_combo.currentData(ServerComboData.JsonMetadata.value)

        if check_server and is_lizmap_dot_com_hosting(server_metadata):
            error, results, more = valid_saas_lizmap_dot_com(self.project)
            if error:
                warnings.append(Warnings.SaasLizmapDotCom.value)

                message = tr('Some configurations are not valid when used with a Lizmap.com hosting :')

                message += "<br><ul>"
                for error in results.values():
                    message += "<li>{}</li>".format(error)
                message += "</ul><br>"

                if more:
                    message += more
                    message += "<br>"

                message += tr(
                    "The process is continuing but expect some layers to not be visible in Lizmap Web Client.")
                ScrollMessageBox(self.dlg, QMessageBox.Warning, tr('Lizmap.com hosting'), message)

        metadata = {
            'qgis_desktop_version': qgis_version(),
            'lizmap_plugin_version_str': current_version,
            'lizmap_plugin_version': int(format_version_integer(current_version)),
            'lizmap_web_client_target_version': int(format_version_integer('{}.0'.format(lwc_version.value))),
            'lizmap_web_client_target_status': target_status.value,
            'instance_target_url': self.dlg.server_combo.currentData(ServerComboData.ServerUrl.value)
        }
        repository = self.dlg.current_repository()
        if repository:
            metadata['instance_target_repository'] = repository

        if valid is not None:
            metadata['project_valid'] = valid
            if not valid:
                warnings.append(Warnings.OgcNotValid.value)

        liz2json = dict()
        liz2json['metadata'] = metadata
        liz2json['warnings'] = warnings
        liz2json["options"] = dict()
        liz2json["layers"] = dict()

        # projection
        projection = self.iface.mapCanvas().mapSettings().destinationCrs()
        liz2json['options']['projection'] = dict()
        liz2json['options']['projection']['proj4'] = projection.toProj()
        liz2json['options']['projection']['ref'] = projection.authid()

        # wms extent
        liz2json['options']['bbox'] = self.project.readListEntry('WMSExtent', '')[0]

        # set initialExtent values if not defined
        if not self.dlg.inInitialExtent.text():
            self.set_initial_extent_from_project()

        # gui user defined options
        for key, item in self.global_options.items():
            if item.get('widget'):
                input_value = None
                # Get field value depending on widget type
                if item['wType'] == 'text':
                    input_value = item['widget'].text().strip(' \t')

                if item['wType'] == 'wysiwyg':
                    input_value = item['widget'].html_content().strip(' \t')

                if item['wType'] == 'textarea':
                    input_value = item['widget'].toPlainText().strip(' \t')

                if item['wType'] == 'spinbox':
                    input_value = item['widget'].value()

                if item['wType'] == 'checkbox':
                    input_value = str(item['widget'].isChecked())

                if item['wType'] == 'list':
                    input_value = item['widget'].currentData()

                if item['wType'] == 'layers':
                    lay = item['widget'].layer(item['widget'].currentIndex())
                    input_value = lay.id()

                if item['wType'] == 'fields':
                    input_value = item['widget'].currentField()

                # Cast value depending of data type
                if item['type'] == 'string':
                    if item['wType'] in ('text', 'textarea'):
                        input_value = str(input_value)
                    else:
                        input_value = str(input_value)

                elif item['type'] in ('intlist', 'floatlist', 'list'):
                    if item['type'] == 'intlist':
                        input_value = [int(a) for a in input_value.split(', ') if a.isdigit()]
                    elif item['type'] == 'floatlist':
                        input_value = [float(a) for a in input_value.split(', ')]
                    else:
                        input_value = [a.strip() for a in input_value.split(',') if a.strip()]

                elif item['type'] == 'integer':
                    # noinspection PyBroadException
                    try:
                        input_value = int(input_value)
                    except Exception:
                        input_value = int(item['default'])

                elif item['type'] == 'boolean':
                    input_value = item['widget'].isChecked()
                    if not item.get('use_proper_boolean'):
                        input_value = str(input_value)

                # Add value to the option
                if item['type'] == 'boolean':
                    if not to_bool(input_value):
                        if not item.get('always_export'):
                            continue

                if item['type'] in ('list', 'string'):
                    if not input_value:
                        # Empty list or string
                        if not item.get('always_export'):
                            continue

                liz2json["options"][key] = input_value

        for key in self.layers_table.keys():
            manager = self.layers_table[key].get('manager')
            if manager:
                data = manager.to_json()

                if key == 'layouts':
                    # The print combobox is removed
                    # Let's remove from the CFG file
                    if lwc_version >= LwcVersions.Lizmap_3_7:
                        try:
                            del liz2json['options']['print']
                        except KeyError:
                            pass
                    else:
                        # We do not want to save this table if it's less than LWC 3.7
                        LOGGER.info("Skipping the 'layout' table because version if less than LWC 3.7")
                        continue

                if manager.use_single_row() and manager.table.rowCount() == 1:
                    liz2json['options'].update(data)
                else:
                    liz2json[key] = data

        # list of Lizmap external baselayers
        table_widget_base_layer = self.dlg.twLizmapBaselayers
        tw_row_count = table_widget_base_layer.rowCount()
        if tw_row_count > 0:
            liz2json["lizmapExternalBaselayers"] = dict()
            for row in range(tw_row_count):
                # check that the layer is checked in the WFS capabilities
                l_repository = table_widget_base_layer.item(row, 0).text()
                l_project = table_widget_base_layer.item(row, 1).text()
                l_name = table_widget_base_layer.item(row, 2).text()
                l_title = table_widget_base_layer.item(row, 3).text()
                l_image_format = table_widget_base_layer.item(row, 4).text()
                if l_image_format not in ('png', 'png; mode=16bit', 'png; mode=8bit', 'jpg', 'jpeg'):
                    l_image_format = 'png'
                liz2json["lizmapExternalBaselayers"][l_name] = {
                    "repository": l_repository,
                    "project": l_project,
                    "layerName": l_name,
                    "layerTitle": l_title,
                    "layerImageFormat": l_image_format,
                    "order": row,
                }

        # gui user defined layers options
        for k, v in self.layerList.items():
            layer = False
            if v['groupAsLayer']:
                layer_type = 'layer'
            else:
                layer_type = 'group'

            if self.get_qgis_layer_by_id(k):
                layer_type = 'layer'

            # ~ # add layerOption only for geo layers
            # ~ if geometryType != 4:
            layer_options = dict()
            layer_options["id"] = str(k)
            layer_options["name"] = str(v['name'])
            layer_options["type"] = layer_type

            geometry_type = -1
            if layer_type == 'layer':
                layer = self.get_qgis_layer_by_id(k)
                if layer and layer.type() == QgsMapLayer.VectorLayer:  # if it is a vector layer:
                    geometry_type = layer.geometryType()

            # geometry type
            if geometry_type != -1:
                layer_options["geometryType"] = self.mapQgisGeometryType[layer.geometryType()]

            # extent
            if layer:
                extent = layer.extent()
                layer_options['extent'] = [
                    extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]
                layer_options['crs'] = layer.crs().authid()

            # styles
            if isinstance(layer, QgsMapLayer):
                ls = layer.styleManager().styles()
                if len(ls) > 1:
                    layer_options['styles'] = ls

            # Loop through the layer options and set properties from the dictionary
            for key, val in self.layer_options_list.items():
                property_value = v[key]
                if val['type'] == 'string':
                    if val['wType'] in ('text', 'textarea'):
                        property_value = str(property_value)
                    elif val['wType'] == 'list' and isinstance(property_value, tuple):
                        # Process later, do not cast for now
                        pass
                    else:
                        property_value = str(property_value)
                elif val['type'] == 'integer':
                    # noinspection PyBroadException
                    try:
                        property_value = int(property_value)
                    except Exception:
                        property_value = 1
                elif val['type'] == 'boolean':
                    if not val.get('use_proper_boolean'):
                        property_value = str(property_value)

                if key in ('legend_image_option', 'noLegendImage'):
                    if layer_options.get('legend_image_option') and key == 'noLegendImage':
                        # Let's skip, the key is already saved
                        continue

                    if layer_options.get('noLegendImage') and key == 'legend_image_option':
                        # Let's skip, the key is already saved
                        continue

                    max_version = val.get('max_version')
                    if max_version and lwc_version > max_version:
                        LOGGER.info("Skipping key '{}' because of max_version.".format(key))
                        continue

                    min_version = val.get('min_version')
                    if min_version and lwc_version < min_version:
                        LOGGER.info("Skipping key '{}' because of min_version.".format(key))
                        continue

                    if key == 'noLegendImage':
                        # We take the value of legend_image_option
                        property_value = str(False)
                        if v['legend_image_option'] == 'disabled':
                            property_value = str(True)
                        if v['legend_image_option'] == 'expand_at_startup' and with_gui:
                            # We keep False
                            QMessageBox.warning(
                                self.dlg,
                                tr('Legend image'),
                                tr(
                                    'Be careful, the option "Expand at startup" for the layer "{layer_name}" is not '
                                    'available for your Lizmap Web Client target version {target}.'
                                ).format(layer_name=k, target=lwc_version.value)
                                + '\n\n'
                                + tr('Falling back to "Hide at startup" in the configuration file.')
                                + '\n\n'
                                + tr('This option is only available for Lizmap Web Client 3.6 and above.')
                            )

                    if isinstance(property_value, tuple):
                        property_value = property_value[0]

                    # LOGGER.info("Saving {} = {} for layer {}".format(key, property_value, k))

                layer_options[key] = property_value

            # Cache Metatile: unset metatileSize if empty
            # this is to avoid, but LWC must change accordingly to avoid using empty metatileSize
            # (2.2.0 does not handle it)

            # unset metatileSize
            meta_tile_size = layer_options.get('metatileSize')
            if meta_tile_size is not None and isinstance(meta_tile_size, str) and not re.match(r'\d,\d', meta_tile_size):
                del layer_options['metatileSize']

            # unset cacheExpiration if False
            cached = layer_options.get('cached')
            if cached and not to_bool(cached):
                del layer_options['cacheExpiration']

            # unset clientCacheExpiration if not needed
            client_cache = layer_options.get('clientCacheExpiration')
            if client_cache and client_cache < 0:
                del layer_options['clientCacheExpiration']

            # unset externalWms if False
            external_wms = layer_options.get('externalWmsToggle')
            if external_wms and not to_bool(external_wms):
                del layer_options['externalWmsToggle']

            # unset source project and repository if needed
            source_repository = layer_options.get('sourceRepository')
            source_project = layer_options.get('sourceProject')
            if not source_repository or not source_project:
                del layer_options['sourceRepository']
                del layer_options['sourceProject']

            # set popupSource to auto if set to lizmap and no lizmap conf found
            if to_bool(layer_options['popup']) and layer_options['popupSource'] == 'lizmap' \
                    and layer_options['popupTemplate'] == '':
                layer_options['popupSource'] = 'auto'

            if layer_options.get("geometryType") in ('point', 'line', 'polygon'):
                if layer_options.get('popupSource') == 'lizmap' and to_bool(layer_options.get('popup')):
                    QMessageBox.warning(
                        self.dlg,
                        tr('Deprecated feature'),
                        tr(
                            'The layer "{}" is vector layer and the popup is a "Lizmap HTML". This kind of popup is '
                            'deprecated for vector layer, you should switch to another kind of popup, for instance to '
                            'a "QGIS HTML maptip". This will be removed in a future version of Lizmap.'
                        ).format(layer_options["name"]),
                        QMessageBox.Ok
                    )

            # Add external WMS options if needed
            if isinstance(layer, QgsMapLayer) and to_bool(layer_options.get('externalWmsToggle', False)):
                # Only for layers stored in disk
                if layer.providerType() == 'wms':
                    wms_params = get_layer_wms_parameters(layer)
                    if wms_params:
                        layer_options['externalAccess'] = wms_params
                    else:
                        layer_options['externalWmsToggle'] = str(False)
                else:
                    layer_options['externalWmsToggle'] = str(False)

            # Add layer options to the json object
            liz2json["layers"][v['name']] = layer_options

        return liz2json

    def clean_project(self):
        """Clean a little the QGIS project.

        Mainly ghost layers for now.
        """
        layers = remove_all_ghost_layers()
        if layers:
            message = tr(
                'Lizmap has found these layers which are ghost layers: {}. '
                'They have been removed. You must save your project.').format(', '.join(layers))
            # noinspection PyUnresolvedReferences
            self.iface.messageBar().pushMessage(
                'Lizmap', message, level=Qgis.Warning, duration=30
            )

    def check_project_validity(self):
        """Project checker about issues that the user might hae when running in LWC."""
        if qgis_version() < 31400:
            return None, None

        validator = QgsProjectServerValidator()
        valid, results = validator.validate(self.project)
        LOGGER.info(f"Project has been detected : {'VALID' if valid else 'NOT valid'} according to OGC validation.")

        if not valid:
            message = tr(
                'The QGIS project is not valid according to OGC standards. You should check '
                'messages in the Project properties -> QGIS Server tab then Test configuration. '
                '{} error(s) have been found').format(len(results))
            # noinspection PyUnresolvedReferences
            self.iface.messageBar().pushMessage(
                'Lizmap', message, level=Qgis.Warning, duration=15
            )

        self.dlg.check_api_key_address()

        return valid, results

    def check_global_project_options(self) -> Tuple[bool, str]:
        """Checks that the needed options are correctly set : relative path, project saved, etc.

        :return: Flag if the project is valid and an error message.
        :rtype: bool, basestring
        """
        base_message = "<br>" + tr("This is needed before using other tabs in the plugin.")
        message = tr('You need to open a QGIS project, using the QGS extension.')
        if not self.project.fileName():
            return False, message + base_message

        if not self.project.fileName().lower().endswith('qgs'):
            message += "\n\n" + tr(
                "Your extension is QGZ. Please save again the project using the other extension.")
            return False, message + base_message

        if QRegExp(r'\s').indexIn(self.project.baseName()) >= 0:
            message = tr(
                "Your file name has a space in its name. The project file name mustn't have a space in its name.")
            return False, message + base_message

        if self.project.baseName() != unaccent(self.project.baseName()):
            message = tr(
                "Your file name has some accents in its name. The project file name mustn't have accents in its name.")
            return False, message + base_message

        message = tr(
            "You mustn't open the QGS file located in your local webdav directory. Please open a local copy of the "
            "project.")
        # Windows
        network_dav = []
        for i in QStorageInfo.mountedVolumes():
            # Mapping table between 'Z:/' and
            # \\demo.snap.lizmap.com@SSL\DavWWWRoot\lizmap_3_6\dav.php\
            if 'dav.php' in i.device().data().decode():
                network_dav.append(i.rootPath())
        if self.project.fileName().startswith(tuple(network_dav)):
            return False, message + base_message

        # Linux : /run/user/1000/gvfs/dav:host=...,ssl=true,....,prefix=%2Flizmap_3_6%2Fdav.php/...tests_projects/..qgs
        if 'dav.php' in self.project.fileName():
            return False, message + base_message

        # Check if Qgis/capitaliseLayerName is set
        settings = QgsSettings()
        if settings.value('Qgis/capitaliseLayerName') and settings.value('Qgis/capitaliseLayerName', type=bool):
            message = tr(
                'Please deactivate the option "Capitalize layer names" in the tab "Canvas and legend" '
                'in the QGIS option dialog, as it could cause issues with Lizmap.')
            return False, message + base_message

        # Check relative/absolute path
        if to_bool(self.project.readEntry('Paths', 'Absolute')[0]):
            message = tr(
                'The project layer paths must be set to relative. '
                'Please change this options in the project settings.')
            return False, message + base_message

        # check if a title has been given in the project QGIS Server tab configuration
        # first set the WMSServiceCapabilities to true
        if not self.project.readEntry('WMSServiceCapabilities', '/')[1]:
            self.project.writeEntry('WMSServiceCapabilities', '/', str(True))
        if self.project.readEntry('WMSServiceTitle', '')[0] == '':
            self.project.writeEntry('WMSServiceTitle', '', self.project.baseName())

        # check if a bbox has been given in the project QGIS Server tab configuration
        project_wms_extent, _ = self.project.readListEntry('WMSExtent', '')
        full_extent = self.iface.mapCanvas().extent()
        if not project_wms_extent:
            project_wms_extent.append(str(full_extent.xMinimum()))
            project_wms_extent.append(str(full_extent.yMinimum()))
            project_wms_extent.append(str(full_extent.xMaximum()))
            project_wms_extent.append(str(full_extent.yMaximum()))
            self.project.writeEntry('WMSExtent', '', project_wms_extent)
        else:
            if not project_wms_extent[0] or not project_wms_extent[1] or not \
                    project_wms_extent[2] or not project_wms_extent[3]:
                project_wms_extent[0] = str(full_extent.xMinimum())
                project_wms_extent[1] = str(full_extent.yMinimum())
                project_wms_extent[2] = str(full_extent.xMaximum())
                project_wms_extent[3] = str(full_extent.yMaximum())
                self.project.writeEntry('WMSExtent', '', project_wms_extent)

        return True, ''

    def ok_button_clicked(self):
        """When the OK button is press, we 'apply' and close the dialog."""
        if not self.save_cfg_file():
            return

        # Only close the dialog if no error
        self.dlg.close()

    def save_cfg_file(
            self,
            lwc_version: LwcVersions = None,
            save_project: bool = None,
            with_gui: bool = True,
    ) -> bool:
        """Save the CFG file.

        Check the user defined data from GUI and save them to both global and project config files.
        """
        variables = self.project.customVariables()
        variables['lizmap_repository'] = self.dlg.current_repository()
        self.project.setCustomVariables(variables)

        self.dlg.check_qgis_version()
        if not lwc_version:
            lwc_version = self.dlg.current_lwc_version()

        defined_env_target = os.getenv('LIZMAP_TARGET_VERSION')
        if defined_env_target:
            LOGGER.warning("Version defined by environment variable : {}".format(defined_env_target))
            lwc_version = LwcVersions.find(defined_env_target)

        lwc_version: LwcVersions

        if self.version_checker:
            # Maybe running from CLI tools about the version_checker object
            self.version_checker.check_outdated_version(lwc_version, with_gui=with_gui)

        if not self.check_dialog_validity():
            LOGGER.debug("Leaving the dialog without valid project and/or server.")
            # noinspection PyUnresolvedReferences
            self.dlg.display_message_bar(
                tr("No project or server"),
                tr('Either you do not have a server reachable for a long time or you do not have a project opened.'),
                Qgis.Warning,
            )
            return False

        stop_process = tr("The process is stopping.")

        if not self.server_manager.check_admin_login_provided() and not self.is_dev_version:
            QMessageBox.critical(
                self.dlg,
                tr('Missing login on a server'),
                '{}\n\n{}\n\n{}'.format(
                    tr(
                        "You have set up a server in the first panel of the plugin, but you have not provided a "
                        "login/password."
                    ),
                    tr("Please go back to the server panel and edit the server to add a login."),
                    stop_process
                ), QMessageBox.Ok)
            return False

        if not self.is_dev_version:
            if not self.server_manager.check_lwc_version(lwc_version.value):
                QMessageBox.critical(
                    self.dlg,
                    tr('Lizmap Target Version'),
                    '{}\n\n{}\n\n{}'.format(
                        tr(
                            "Your Lizmap Web Client target version {version} has not been found in the server "
                            "table.").format(version=lwc_version.value),
                        tr(
                            "Either check your Lizmap Web Client target version in the first panel of the plugin or "
                            "check you have provided the correct server URL."
                        ),
                        stop_process
                    ), QMessageBox.Ok)
                return False

        if lwc_version >= LwcVersions.Lizmap_3_6:
            LOGGER.info(
                'Lizmap Web Client target version {}, let\'s try to make the project valid.'.format(
                    lwc_version.value)
            )
            # Set shortnames if it's not set
            ogc_projet_validity = OgcProjectValidity(self.project)
            ogc_projet_validity.add_shortnames()
            ogc_projet_validity.set_project_short_name()

            validator = QgsProjectServerValidator()
            valid, results = validator.validate(self.project)
            LOGGER.info(f"Project has been detected : {'VALID' if valid else 'NOT valid'} according to OGC validation.")
        else:
            LOGGER.info(
                "Lizmap Web Client target version {}, we do not update the project for OGC validity.".format(
                    lwc_version.value)
            )

        # global project option checking
        is_valid, message = self.check_global_project_options()
        if not is_valid:
            QMessageBox.critical(
                self.dlg, tr('Lizmap Error'), '{}\n\n{}'.format(message, stop_process), QMessageBox.Ok)
            return False

        # Get configuration from input fields

        # Need to get these values to check for Pseudo Mercator projection
        mercator_layers = [
            self.dlg.cbOsmMapnik.isChecked(),
            self.dlg.cbOsmStamenToner.isChecked(),
            self.dlg.cb_open_topo_map.isChecked(),
            self.dlg.cbGoogleStreets.isChecked(),
            self.dlg.cbGoogleSatellite.isChecked(),
            self.dlg.cbGoogleHybrid.isChecked(),
            self.dlg.cbGoogleTerrain.isChecked(),
            self.dlg.cbBingStreets.isChecked(),
            self.dlg.cbBingSatellite.isChecked(),
            self.dlg.cbBingHybrid.isChecked(),
            self.dlg.cbIgnStreets.isChecked(),
            self.dlg.cbIgnSatellite.isChecked(),
            self.dlg.cbIgnTerrain.isChecked(),
            self.dlg.cbIgnCadastral.isChecked(),
        ]

        self.dlg.outLog.append('=' * 20)
        self.dlg.outLog.append('<b>' + tr('Map - options') + '</b>')
        self.dlg.outLog.append('=' * 20)

        # Checking configuration data
        # Get the project data from api to check the "coordinate system restriction" of the WMS Server settings

        # public base-layers: check that the 3857 projection is set in the
        # "Coordinate System Restriction" section of the project WMS Server tab properties
        if True in mercator_layers:
            crs_list = self.project.readListEntry('WMSCrsList', '')
            mercator_found = False
            for i in crs_list[0]:
                if i == 'EPSG:3857':
                    mercator_found = True
            if not mercator_found:
                crs_list[0].append('EPSG:3857')
                self.project.writeEntry('WMSCrsList', '', crs_list[0])

        # write data in the lizmap json config file
        self.write_project_config_file(lwc_version, with_gui)

        self.log(
            tr('All the map parameters are correctly set'),
            abort=False,
            textarea=self.dlg.outLog)
        self.log(
            '<b>' + tr('Lizmap configuration file has been updated') + '</b>',
            abort=False,
            textarea=self.dlg.outLog)

        self.get_min_max_scales()
        msg = tr('Lizmap configuration file has been updated')

        # Ask to save the project
        auto_save = self.dlg.checkbox_save_project.isChecked()
        if save_project is None:
            # Only save when we are in GUI
            QgsSettings().setValue('lizmap/auto_save_project', auto_save)

        if self.project.isDirty():
            if save_project or auto_save:
                # Do not use QgsProject.write() as it will trigger file
                # modified warning in QGIS Desktop later
                self.iface.actionSaveProject().trigger()
            else:
                # noinspection PyUnresolvedReferences
                self.iface.messageBar().pushMessage(
                    'Lizmap',
                    tr('Please do not forget to save the QGIS project before publishing your map'),
                    level=Qgis.Warning,
                    duration=30
                )

        if auto_save and self.dlg.checkbox_ftp_transfer.isChecked():
            valid, message = self.server_ftp.connect(send_files=True)
            if not valid:
                # noinspection PyUnresolvedReferences
                self.iface.messageBar().pushMessage(
                    'Lizmap',
                    message,
                    level=Qgis.Critical,
                )
                return False

            msg = tr(
                'Lizmap configuration file has been updated and sent to the FTP {}.'
            ).format(self.server_ftp.host)

        # noinspection PyUnresolvedReferences
        self.iface.messageBar().pushMessage(
            'Lizmap',
            msg,
            level=Qgis.Success,
            duration=3
        )
        return True

    def check_visibility_crs_3857(self):
        """ Check if we display the warning about scales. """
        visible = False
        for item in self.crs_3857_base_layers_list.values():
            if item.isChecked():
                visible = True

        self.dlg.scales_warning_layout.setVisible(visible)

    def on_baselayer_checkbox_change(self):
        """
        Add or remove a base-layer in cbStartupBaselayer combobox
        when user change state of any base-layer related checkbox
        """
        if not self.layerList:
            return

        # Combo to fill up with base-layer
        combo = self.dlg.cbStartupBaselayer

        # First get selected item
        idx = combo.currentIndex()
        data = combo.itemData(idx)

        # Clear the combo
        combo.clear()
        i = 0
        blist = []

        # Fill with checked base-layers
        # 1/ QGIS layers
        for k, v in self.layerList.items():
            if not v['baseLayer']:
                continue
            combo.addItem(v['name'], v['name'])
            blist.append(v['name'])
            if data == k:
                idx = i
            i += 1

        # 2/ External base-layers
        for k, v in self.base_layer_widget_list.items():
            if k != 'layer':
                if v.isChecked():
                    combo.addItem(k, k)
                    blist.append(k)
                    if data == k:
                        idx = i
                    i += 1

        # Set last chosen item
        combo.setCurrentIndex(idx)

        # Fill self.globalOptions
        self.global_options['startupBaselayer']['list'] = blist

    def set_startup_baselayer_from_config(self):
        """
        Read lizmap current cfg configuration
        and set the startup base-layer if found
        """
        if not self.dlg.check_cfg_file_exists():
            return

        with open(self.dlg.cfg_file(), encoding='utf8') as f:
            json_file_reader = f.read()

        # noinspection PyBroadException
        try:
            json_content = json.loads(json_file_reader)
            json_options = json_content['options']

            base_layer = json_options.get('startupBaselayer')
            if not base_layer:
                return

            i = self.dlg.cbStartupBaselayer.findData(base_layer)
            if i < 0:
                return

            self.dlg.cbStartupBaselayer.setCurrentIndex(i)
        except Exception:
            pass

    def reinit_default_properties(self):
        for key in self.layers_table.keys():
            self.layers_table[key]['jsonConfig'] = dict()

    def on_project_read(self):
        """
        Close Lizmap plugin when project is opened
        """
        self.reinit_default_properties()
        self.dlg.close()

    def run(self) -> bool:
        """Plugin run method : launch the GUI."""
        if self.dlg.isVisible():
            # show dialog in front of QGIS
            self.dlg.raise_()
            self.dlg.activateWindow()
            return False

        # QGIS Plugin manager
        qgis_plugin_manager = None
        if QGIS_PLUGIN_MANAGER:
            # noinspection PyBroadException
            try:
                plugin_manager = PluginManager()
                self.dlg.label_lizmap_plugin.setText(plugin_manager.lizmap_version())
                self.dlg.label_wfsoutputextension_plugin.setText(plugin_manager.wfs_output_extension_version())
                self.dlg.label_atlasprint_plugin.setText(plugin_manager.atlas_print_version())
                qgis_plugin_manager = True
            except Exception as e:
                # Core QGIS plugin manager API might not be well stable ?
                LOGGER.warning("Exception when reading the QGIS plugin manager : {}".format(str(e)))
        if not qgis_plugin_manager:
            # self.dlg.label_lizmap_plugin.setText("Lizmap - Unknown")
            # self.dlg.label_wfsoutputextension_plugin.setText("WfsOutputExtension - Unknown")
            # self.dlg.label_atlasprint_plugin.setText("AtlasPrint - Unknown")
            self.dlg.label_lizmap_plugin.setVisible(False)
            self.dlg.label_wfsoutputextension_plugin.setVisible(False)
            self.dlg.label_atlasprint_plugin.setVisible(False)
            self.dlg.label_qgis_server_plugins.setVisible(False)

        self.version_checker = VersionChecker(self.dlg, VERSION_URL)
        self.version_checker.fetch()

        if not self.check_dialog_validity():
            # Go back to the first panel because no project loaded.
            # Otherwise, the plugin opens the latest valid panel before the previous project has been closed.
            self.dlg.mOptionsListWidget.setCurrentRow(0)

        self.dlg.show()

        # Get config file data
        self.read_cfg_file()

        auto_save = QgsSettings().value('lizmap/auto_save_project', False, bool)
        self.dlg.checkbox_save_project.setChecked(auto_save)

        self.dlg.exec_()
        return True
