<?php
/**
 * IVR Manager — delete a managed time condition (and all its closures).
 * Refuses to touch a dialplan the app did not create (ownership marker).
 */
require_once "root.php";
require_once "resources/require.php";
require_once "resources/check_auth.php";
require_once __DIR__ . "/resources/functions.php";
require_once __DIR__ . "/resources/classes/ivr_schedule.php";

if (!permission_exists('ivr_manager_schedule_delete')) {
	echo "access denied";
	exit;
}

$pdo = ivrmgr_pdo();
$domain_uuid = $_SESSION['domain_uuid'];
$domain_name = $_SESSION['domain_name'];

$ext = isset($_GET['ext']) ? (int) $_GET['ext'] : 0;
$engine = new ivr_schedule($pdo, $domain_uuid, $domain_name);

try {
	if ($ext && $engine->delete_schedule($ext)) {
		ivrmgr_reloadxml();
		ivrmgr_message('Deleted.');
	} else {
		ivrmgr_message('Nothing deleted.', 'negative');
	}
} catch (Exception $e) {
	ivrmgr_message('Refused: ' . $e->getMessage(), 'negative');
}

header('Location: schedules.php');
exit;
