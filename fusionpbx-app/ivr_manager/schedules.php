<?php
/**
 * IVR Manager — list managed time conditions (schedules) and their closures.
 */
require_once "root.php";
require_once "resources/require.php";
require_once "resources/check_auth.php";

if (!permission_exists('ivr_manager_schedule_view')) {
	echo "access denied";
	exit;
}

// multilingual + db
$language = new text;
$text = $language->get();
$database = new database;

$domain_uuid = $_SESSION['domain_uuid'];
$domain_name = $_SESSION['domain_name'];
$rec_dir = (isset($_SESSION['switch']['recordings']['dir']) ? $_SESSION['switch']['recordings']['dir'] : '/var/lib/freeswitch/recordings') . '/' . $domain_name;

$engine = new ivr_schedule($database->db, $domain_uuid, $domain_name, $rec_dir);
$schedules = $engine->list_schedules();

$document['title'] = 'IVR Manager';
require_once "resources/header.php";

echo "<div class='action_bar' id='action_bar'>\n";
echo "	<div class='heading'><b>IVR Manager — Time Conditions</b></div>\n";
echo "	<div class='actions'>\n";
if (permission_exists('ivr_manager_schedule_add')) {
	echo button::create(array('type' => 'button', 'label' => 'Add', 'icon' => 'plus', 'link' => 'schedule_edit.php'));
}
echo "	</div>\n";
echo "	<div style='clear: both;'></div>\n";
echo "</div>\n";

echo "<table class='list'>\n";
echo "<tr class='list-header'>\n";
echo "	<th>Extension</th><th>Name</th><th>Closures</th><th>Open destination</th><th class='center'>Enabled</th><th class='action-button'>&nbsp;</th>\n";
echo "</tr>\n";

foreach ($schedules as $s) {
	$ext = (int) $s['extension'];
	echo "<tr class='list-row'>\n";
	echo "	<td>" . escape($ext) . "</td>\n";
	echo "	<td>" . escape($s['label']) . "</td>\n";
	echo "	<td>";
	if (count($s['closures'])) {
		echo "<ul style='margin:0; padding-left:1.1em;'>";
		foreach ($s['closures'] as $c) {
			echo "<li><b>" . escape($c['label']) . "</b>: "
				. escape($c['start']) . " &rarr; " . escape($c['end'])
				. " (" . escape($c['closed_action']) . ")</li>";
		}
		echo "</ul>";
	} else {
		echo "<span class='description'>none</span>";
	}
	echo "</td>\n";
	echo "	<td>" . escape($s['open_destination']) . "</td>\n";
	echo "	<td class='center'>" . ($s['enabled'] ? 'true' : 'false') . "</td>\n";
	echo "	<td class='action-button'>";
	if (permission_exists('ivr_manager_schedule_edit')) {
		echo button::create(array('type' => 'button', 'title' => 'Edit', 'icon' => 'pencil-alt', 'link' => 'schedule_edit.php?ext=' . urlencode($ext)));
	}
	if (permission_exists('ivr_manager_schedule_delete')) {
		echo button::create(array('type' => 'button', 'title' => 'Delete', 'icon' => 'trash', 'link' => 'schedule_delete.php?ext=' . urlencode($ext), 'onclick' => "return confirm('Delete time condition " . $ext . " and all its closures?');"));
	}
	echo "</td>\n";
	echo "</tr>\n";
}
echo "</table>\n";

if (!count($schedules)) {
	echo "<p class='description'>No managed time conditions yet.</p>\n";
}

require_once "resources/footer.php";
