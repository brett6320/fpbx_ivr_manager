<?php
/**
 * IVR Manager — create/edit a time condition holding one or more closures.
 */
require_once "root.php";
require_once "resources/require.php";
require_once "resources/check_auth.php";

if (!permission_exists('ivr_manager_schedule_add') && !permission_exists('ivr_manager_schedule_edit')) {
	echo "access denied";
	exit;
}

$language = new text;
$text = $language->get();
$database = new database;

$domain_uuid = $_SESSION['domain_uuid'];
$domain_name = $_SESSION['domain_name'];
$rec_dir = (isset($_SESSION['switch']['recordings']['dir']) ? $_SESSION['switch']['recordings']['dir'] : '/var/lib/freeswitch/recordings') . '/' . $domain_name;
$engine = new ivr_schedule($database->db, $domain_uuid, $domain_name, $rec_dir);

// pool bounds from default settings (fallback 9550-9599)
$pool_start = isset($_SESSION['ivr_manager']['extension_pool_start']['numeric']) ? (int) $_SESSION['ivr_manager']['extension_pool_start']['numeric'] : 9550;
$pool_end = isset($_SESSION['ivr_manager']['extension_pool_end']['numeric']) ? (int) $_SESSION['ivr_manager']['extension_pool_end']['numeric'] : 9599;

// recordings for the greeting picker
$stmt = $database->db->prepare("select recording_name, recording_filename from v_recordings where domain_uuid = :d and recording_filename is not null order by recording_name");
$stmt->execute(array(':d' => $domain_uuid));
$recordings = $stmt->fetchAll(PDO::FETCH_ASSOC);

$ext = isset($_GET['ext']) && $_GET['ext'] !== '' ? (int) $_GET['ext'] : null;

// ---- POST: save ----
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
	$token = new token;
	if (!$token->validate($_SERVER['PHP_SELF'])) {
		message::add('Invalid token', 'negative');
		header('Location: schedules.php');
		exit;
	}
	$name = trim($_POST['name']);
	$open = trim($_POST['open_destination']);
	$extension = ($_POST['extension'] !== '') ? (int) $_POST['extension'] : allocate_extension($database->db, $domain_uuid, $pool_start, $pool_end);

	$closures = array();
	$labels = isset($_POST['c_label']) ? $_POST['c_label'] : array();
	foreach ($labels as $i => $label) {
		$label = trim($label);
		$start = isset($_POST['c_start'][$i]) ? $_POST['c_start'][$i] : '';
		$end = isset($_POST['c_end'][$i]) ? $_POST['c_end'][$i] : '';
		if ($label === '' || $start === '' || $end === '') {
			continue;
		}
		$closures[] = array(
			'label' => $label,
			'start' => to_fs_datetime($start),
			'end' => to_fs_datetime($end),
			'reason' => isset($_POST['c_reason'][$i]) ? trim($_POST['c_reason'][$i]) : '',
			'closed_action' => (isset($_POST['c_action'][$i]) && $_POST['c_action'][$i] === 'hangup') ? 'hangup' : 'voicemail',
			'recording_filename' => isset($_POST['c_recording'][$i]) ? $_POST['c_recording'][$i] : '',
		);
	}

	if ($name === '' || $open === '' || !count($closures)) {
		message::add('A name, open destination and at least one closure are required.', 'negative');
	} else {
		try {
			$engine->upsert($extension, $name, $closures, $open);
			// reload the dialplan so FreeSWITCH applies the change
			ivr_manager_reloadxml();
			message::add('Saved.');
			header('Location: schedules.php');
			exit;
		} catch (Exception $e) {
			message::add('Refused: ' . $e->getMessage(), 'negative');
		}
	}
}

// ---- prefill for edit ----
$current = $ext !== null ? $engine->get_schedule($ext) : null;
$rows = ($current && count($current['closures'])) ? $current['closures'] : array(array('label' => '', 'start' => '', 'end' => '', 'reason' => '', 'closed_action' => 'voicemail', 'recording_filename' => ''));

$document['title'] = 'IVR Manager';
require_once "resources/header.php";

$token = new token;
$t = $token->create($_SERVER['PHP_SELF']);

echo "<form method='post' action='schedule_edit.php" . ($ext !== null ? '?ext=' . urlencode($ext) : '') . "'>\n";
echo "<div class='action_bar'><div class='heading'><b>" . ($current ? 'Edit' : 'New') . " time condition</b></div>";
echo "<div class='actions'>" . button::create(array('type' => 'button', 'label' => 'Back', 'icon' => 'chevron-left', 'link' => 'schedules.php'));
echo button::create(array('type' => 'submit', 'label' => 'Save', 'icon' => 'check')) . "</div><div style='clear:both;'></div></div>\n";

echo "<table width='100%'>\n";
echo "<tr><td class='vncell'>Name</td><td class='vtable'><input class='formfld' name='name' value='" . escape($current ? $current['label'] : '') . "' required></td></tr>\n";
echo "<tr><td class='vncell'>Open destination</td><td class='vtable'><input class='formfld' name='open_destination' placeholder='e.g. 2000' value='" . escape($current ? $current['open_destination'] : '') . "' required></td></tr>\n";
echo "<tr><td class='vncell'>Extension</td><td class='vtable'><input class='formfld' name='extension' value='" . ($ext !== null ? escape($ext) : '') . "'" . ($ext !== null ? ' readonly' : '') . " placeholder='blank = auto (" . $pool_start . "-" . $pool_end . ")'></td></tr>\n";
echo "</table>\n";

echo "<br><b>Closures</b> <span class='description'>most-specific (shortest) window matches first</span>\n";
echo "<table class='list' id='closures'>\n";
echo "<tr class='list-header'><th>Label</th><th>Closed from</th><th>Closed until</th><th>Reason</th><th>When closed</th><th>Greeting</th></tr>\n";
foreach ($rows as $c) {
	echo closure_row_html($c, $recordings);
}
echo "</table>\n";
echo "<input type='button' class='btn' value='+ Add closure' onclick='ivrmgrAddRow();'>\n";
echo "<input type='hidden' name='" . $token['name'] . "' value='" . $token['hash'] . "'>\n";
echo "</form>\n";

// row template for the add button
echo "<template id='closure_tpl'>" . closure_row_html(array('label' => '', 'start' => '', 'end' => '', 'reason' => '', 'closed_action' => 'voicemail', 'recording_filename' => ''), $recordings) . "</template>\n";
?>
<script>
function ivrmgrAddRow(){
	var tpl = document.getElementById('closure_tpl').innerHTML;
	var tbody = document.getElementById('closures');
	tbody.insertAdjacentHTML('beforeend', tpl);
}
</script>
<?php
require_once "resources/footer.php";

// ---- helpers ----
function to_fs_datetime($v) {
	// datetime-local "Y-m-dTH:i" -> "Y-m-d H:i:s"
	$v = str_replace('T', ' ', trim($v));
	if (strlen($v) === 16) { $v .= ':00'; }
	return $v;
}
function to_input_datetime($fs) {
	if (!$fs) { return ''; }
	return substr(str_replace(' ', 'T', $fs), 0, 16);
}
function closure_row_html($c, $recordings) {
	$sel_start = to_input_datetime(isset($c['start']) ? $c['start'] : '');
	$sel_end = to_input_datetime(isset($c['end']) ? $c['end'] : '');
	$h = "<tr class='list-row'>";
	$h .= "<td><input class='formfld' name='c_label[]' value='" . escape($c['label']) . "' placeholder='July 4th'></td>";
	$h .= "<td><input class='formfld' type='datetime-local' name='c_start[]' value='" . escape($sel_start) . "'></td>";
	$h .= "<td><input class='formfld' type='datetime-local' name='c_end[]' value='" . escape($sel_end) . "'></td>";
	$h .= "<td><input class='formfld' name='c_reason[]' value='" . escape(isset($c['reason']) ? $c['reason'] : '') . "' placeholder='a holiday'></td>";
	$act = isset($c['closed_action']) ? $c['closed_action'] : 'voicemail';
	$h .= "<td><select class='formfld' name='c_action[]'>"
		. "<option value='voicemail'" . ($act === 'voicemail' ? ' selected' : '') . ">voicemail</option>"
		. "<option value='hangup'" . ($act === 'hangup' ? ' selected' : '') . ">hangup</option></select></td>";
	$h .= "<td><select class='formfld' name='c_recording[]'><option value=''>— select —</option>";
	foreach ($recordings as $r) {
		$sel = ($r['recording_filename'] === (isset($c['recording_filename']) ? $c['recording_filename'] : '')) ? ' selected' : '';
		$h .= "<option value='" . escape($r['recording_filename']) . "'" . $sel . ">" . escape($r['recording_name']) . "</option>";
	}
	$h .= "</select></td>";
	$h .= "</tr>";
	return $h;
}
function allocate_extension($pdo, $domain_uuid, $start, $end) {
	$used = array();
	$stmt = $pdo->prepare("select dialplan_number as n from v_dialplans where domain_uuid = :d union select extension as n from v_extensions where domain_uuid = :d");
	$stmt->execute(array(':d' => $domain_uuid));
	foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $r) {
		if (is_numeric($r['n'])) { $used[(int) $r['n']] = true; }
	}
	for ($n = $start; $n <= $end; $n++) {
		if (!isset($used[$n])) { return $n; }
	}
	throw new Exception('extension pool ' . $start . '-' . $end . ' exhausted');
}
function ivr_manager_reloadxml() {
	// best-effort: FusionPBX exposes event_socket; fall back silently
	if (class_exists('event_socket')) {
		try {
			$esl = new event_socket;
			if ($esl->connect()) {
				$esl->request('api reloadxml');
			}
		} catch (Exception $e) {
			// ignore — admin can reload from the GUI
		}
	}
}
