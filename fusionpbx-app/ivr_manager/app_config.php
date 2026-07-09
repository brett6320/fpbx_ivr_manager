<?php
/**
 * FusionPBX app definition for IVR Manager (PHP variant).
 *
 * Read by "Advanced → Upgrade → App Defaults" (and Menu Manager → Restore) to
 * register the app's permissions, menu item and default settings. Compatible
 * with the FusionPBX 4.x and 5.x app framework.
 */
if (!defined('STDIN')) {
	// no direct web execution
}

$apps[] = array(
	'name' => 'IVR Manager',
	'uuid' => 'b185f24c-60eb-4c4f-90b6-ffc510d280e6',
	'category' => 'Switch',
	'subcategory' => '',
	'version' => '1.0',
	'license' => 'MIT',
	'url' => 'https://github.com/brett6320/fpbx_ivr_manager',
	'description' => array(
		'en-us' => 'Manage planned office-closure time conditions (with multiple closures per condition) and IVR menus, natively inside FusionPBX.',
	),

	// ---- permissions ----
	'permissions' => array(
		array('name' => 'ivr_manager_schedule_view',
			'groups' => array('superadmin', 'admin', 'user')),
		array('name' => 'ivr_manager_schedule_add',
			'groups' => array('superadmin', 'admin', 'user')),
		array('name' => 'ivr_manager_schedule_edit',
			'groups' => array('superadmin', 'admin', 'user')),
		array('name' => 'ivr_manager_schedule_delete',
			'groups' => array('superadmin', 'admin')),
		array('name' => 'ivr_manager_tts_manage',
			'groups' => array('superadmin', 'admin')),
		array('name' => 'ivr_manager_business_manage',
			'groups' => array('superadmin', 'admin')),
	),

	// ---- menu (top level; relocate via Menu Manager if desired) ----
	'menu' => array(
		array(
			'title' => array('en-us' => 'IVR Manager'),
			'uuid' => 'd2de91d6-d845-4db2-97aa-cf63866b679e',
			'parent_uuid' => '',
			'category' => 'internal',
			'icon' => 'calendar-times',
			'path' => '/app/ivr_manager/schedules.php',
			'order' => '30',
			'groups' => array('superadmin', 'admin', 'user'),
		),
	),

	// ---- default settings ----
	'default_settings' => array(
		array(
			'default_setting_uuid' => 'a3f6b0d2-2c1e-4f77-9a2a-2d3f4c5b6a70',
			'default_setting_category' => 'ivr_manager',
			'default_setting_subcategory' => 'extension_pool_start',
			'default_setting_name' => 'numeric',
			'default_setting_value' => '9550',
			'default_setting_enabled' => 'true',
			'default_setting_description' => 'First extension in the managed pool for auto-allocated schedules/IVRs.',
		),
		array(
			'default_setting_uuid' => 'b4a71c33-3d2f-40aa-8b3b-3e4a5b6c7d81',
			'default_setting_category' => 'ivr_manager',
			'default_setting_subcategory' => 'extension_pool_end',
			'default_setting_name' => 'numeric',
			'default_setting_value' => '9599',
			'default_setting_enabled' => 'true',
			'default_setting_description' => 'Last extension in the managed pool.',
		),
	),
);
