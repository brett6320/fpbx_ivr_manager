<?php
/**
 * IVR Manager — list managed time conditions (schedules) and their closures.
 */
require_once "root.php";
require_once "resources/require.php";
require_once "resources/check_auth.php";
require_once __DIR__ . "/resources/functions.php";
require_once __DIR__ . "/resources/classes/ivr_schedule.php";

if (!permission_exists('ivr_manager_schedule_view')) {
	echo "access denied";
	exit;
}

// multilingual + db
$language = new text;
$text = $language->get();
$pdo = ivrmgr_pdo();

$domain_uuid = $_SESSION['domain_uuid'];
$domain_name = $_SESSION['domain_name'];

$engine = new ivr_schedule($pdo, $domain_uuid, $domain_name);
$schedules = $engine->list_schedules();

$document['title'] = 'IVR Manager';
require_once "resources/header.php";

ivrmgr_render_flash();

echo "<div class='action_bar' id='action_bar'>\n";
echo "	<div class='heading'><b>IVR Manager — Time Conditions</b></div>\n";
echo "	<div class='actions'>\n";
if (permission_exists('ivr_manager_schedule_add')) {
	echo ivrmgr_button(array('type' => 'button', 'label' => 'Add', 'icon' => 'plus', 'link' => 'schedule_edit.php'));
}
if (permission_exists('ivr_manager_business_manage')) {
	echo " " . ivrmgr_button(array('type' => 'button', 'label' => 'Business', 'icon' => 'building', 'link' => 'business_settings.php'));
}
if (permission_exists('ivr_manager_tts_manage')) {
	echo " " . ivrmgr_button(array('type' => 'button', 'label' => 'TTS settings', 'icon' => 'volume-up', 'link' => 'tts_settings.php'));
}
echo "	</div>\n";
echo "	<div style='clear: both;'></div>\n";
echo "</div>\n";

echo "<table class='list' width='100%'>\n";
echo "<tr class='list-header'>\n";
echo "	<th>Extension</th><th>Name</th><th>Closures</th><th>Open destination</th><th class='center'>Enabled</th><th class='action-button'>&nbsp;</th>\n";
echo "</tr>\n";

foreach ($schedules as $s) {
	$ext = (int) $s['extension'];
	echo "<tr class='list-row'>\n";
	echo "	<td>" . ivrmgr_esc($ext) . "</td>\n";
	echo "	<td>" . ivrmgr_esc($s['label']) . "</td>\n";
	echo "	<td>";
	if (count($s['closures'])) {
		echo "<ul style='margin:0; padding-left:1.1em;'>";
		foreach ($s['closures'] as $c) {
			echo "<li><b>" . ivrmgr_esc($c['label']) . "</b>: "
				. ivrmgr_esc($c['start']) . " &rarr; " . ivrmgr_esc($c['end'])
				. " (" . ivrmgr_esc($c['closed_action']) . ")</li>";
		}
		echo "</ul>";
	} else {
		echo "<span class='description'>none</span>";
	}
	echo "</td>\n";
	echo "	<td>" . ivrmgr_esc($s['open_destination']) . "</td>\n";
	echo "	<td class='center'>" . ($s['enabled'] ? 'true' : 'false') . "</td>\n";
	echo "	<td class='action-button'>";
	if (permission_exists('ivr_manager_schedule_edit')) {
		echo ivrmgr_button(array('type' => 'button', 'title' => 'Edit', 'label' => 'Edit', 'icon' => 'pencil-alt', 'link' => 'schedule_edit.php?ext=' . urlencode($ext)));
	}
	if (permission_exists('ivr_manager_schedule_delete')) {
		// plain confirm-anchor: identical behavior on 4.5.x and 5.x
		echo "<a href='schedule_delete.php?ext=" . urlencode($ext) . "' class='btn' "
			. "onclick=\"return confirm('Delete time condition " . $ext . " and all its closures?');\">Delete</a>";
	}
	echo "</td>\n";
	echo "</tr>\n";
}
echo "</table>\n";

if (!count($schedules)) {
	echo "<p class='description'>No managed time conditions yet.</p>\n";
}

require_once "resources/footer.php";
