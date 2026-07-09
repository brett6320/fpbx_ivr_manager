<?php
/**
 * IVR Manager — business configuration. Default destinations used to prefill
 * schedules: on-hours (normal daytime), off-hours, and emergency.
 */
require_once "root.php";
require_once "resources/require.php";
require_once "resources/check_auth.php";
require_once __DIR__ . "/resources/functions.php";
require_once __DIR__ . "/resources/classes/ivr_schedule.php";
require_once __DIR__ . "/resources/classes/ivr_settings.php";
require_once __DIR__ . "/resources/classes/ivr_destinations.php";

if (!permission_exists('ivr_manager_business_manage')) {
	echo "access denied";
	exit;
}

$language = new text;
$text = $language->get();
$pdo = ivrmgr_pdo();
$domain_uuid = $_SESSION['domain_uuid'];

$ivrmgr_settings = new ivr_settings($pdo, $domain_uuid);
$dest = new ivr_destinations($pdo, $domain_uuid);

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
	$token = new token;
	if (!$token->validate($_SERVER['PHP_SELF'])) {
		ivrmgr_message('Invalid token', 'negative');
		header('Location: business_settings.php');
		exit;
	}
	$ivrmgr_settings->set('dest_on_hours', ivr_destinations::resolve($_POST, 'dest_on_hours'));
	$ivrmgr_settings->set('dest_off_hours', ivr_destinations::resolve($_POST, 'dest_off_hours'));
	$ivrmgr_settings->set('dest_emergency', ivr_destinations::resolve($_POST, 'dest_emergency'));
	ivrmgr_message('Saved.');
	header('Location: business_settings.php');
	exit;
}

$on = $ivrmgr_settings->get('dest_on_hours', '');
$off = $ivrmgr_settings->get('dest_off_hours', '');
$emerg = $ivrmgr_settings->get('dest_emergency', '');

$document['title'] = 'IVR Manager — Business';
require_once "resources/header.php";
ivrmgr_render_flash();

$token = new token;
$t = $token->create($_SERVER['PHP_SELF']);

echo "<div class='action_bar'><div class='heading'><b>Business configuration</b></div>";
echo "<div class='actions'>" . ivrmgr_button(array('type' => 'button', 'label' => 'Back', 'icon' => 'chevron-left', 'link' => 'schedules.php')) . "</div><div style='clear:both;'></div></div>\n";

echo "<p class='description'>Default destinations, reusable when building schedules. "
	. "Each may be a time condition, ring group, IVR, extension or voicemail.</p>\n";

echo "<form method='post' action='business_settings.php'>\n";
echo "<table width='100%'>\n";
echo "<tr><td class='vncell'>On-hours (normal daytime)</td><td class='vtable'>" . $dest->render_select('dest_on_hours', $on) . "</td></tr>\n";
echo "<tr><td class='vncell'>Off-hours</td><td class='vtable'>" . $dest->render_select('dest_off_hours', $off) . "</td></tr>\n";
echo "<tr><td class='vncell'>Emergency</td><td class='vtable'>" . $dest->render_select('dest_emergency', $emerg) . "</td></tr>\n";
echo "</table>\n";
echo "<input type='hidden' name='" . $t['name'] . "' value='" . $t['hash'] . "'>\n";
echo "<button type='submit' class='btn'>Save</button>\n";
echo "</form>\n";

require_once "resources/footer.php";
