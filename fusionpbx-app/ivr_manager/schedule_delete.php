<?php
/**
 * IVR Manager — delete a managed time condition (and all its closures).
 * Refuses to touch a dialplan the app did not create (ownership marker).
 */
require_once "root.php";
require_once "resources/require.php";
require_once "resources/check_auth.php";

if (!permission_exists('ivr_manager_schedule_delete')) {
	echo "access denied";
	exit;
}

$database = new database;
$domain_uuid = $_SESSION['domain_uuid'];
$domain_name = $_SESSION['domain_name'];
$rec_dir = (isset($_SESSION['switch']['recordings']['dir']) ? $_SESSION['switch']['recordings']['dir'] : '/var/lib/freeswitch/recordings') . '/' . $domain_name;

$ext = isset($_GET['ext']) ? (int) $_GET['ext'] : 0;
$engine = new ivr_schedule($database->db, $domain_uuid, $domain_name, $rec_dir);

try {
	if ($ext && $engine->delete_schedule($ext)) {
		if (class_exists('event_socket')) {
			try {
				$esl = new event_socket;
				if ($esl->connect()) {
					$esl->request('api reloadxml');
				}
			} catch (Exception $e) {
				// ignore — reload from the GUI if needed
			}
		}
		message::add('Deleted.');
	} else {
		message::add('Nothing deleted.', 'negative');
	}
} catch (Exception $e) {
	message::add('Refused: ' . $e->getMessage(), 'negative');
}

header('Location: schedules.php');
exit;
