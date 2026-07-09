<?php
/**
 * IVR Manager — Google TTS settings. The service-account JSON is WRITE-ONLY:
 * it can be set/replaced and tested, but is never rendered back to the browser.
 */
require_once "root.php";
require_once "resources/require.php";
require_once "resources/check_auth.php";
require_once __DIR__ . "/resources/functions.php";
require_once __DIR__ . "/resources/classes/ivr_schedule.php";
require_once __DIR__ . "/resources/classes/ivr_settings.php";
require_once __DIR__ . "/resources/classes/ivr_tts.php";

if (!permission_exists('ivr_manager_tts_manage')) {
	echo "access denied";
	exit;
}

$language = new text;
$text = $language->get();
$pdo = ivrmgr_pdo();
$domain_uuid = $_SESSION['domain_uuid'];
$ivrmgr_settings = new ivr_settings($pdo, $domain_uuid);

$test_result = null;

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
	$token = new token;
	if (!$token->validate($_SERVER['PHP_SELF'])) {
		ivrmgr_message('Invalid token', 'negative');
		header('Location: tts_settings.php');
		exit;
	}
	$action = isset($_POST['action']) ? $_POST['action'] : 'save';
	$voice = trim($_POST['voice']);
	$lang = trim($_POST['language']);
	$pasted = trim($_POST['credentials']);   // may be empty = "keep current"

	// voice/language are not secret — persist them
	$ivrmgr_settings->set('tts_voice', $voice);
	$ivrmgr_settings->set('tts_language', $lang);

	// which creds to act on: freshly pasted (if any), else the stored ones
	$creds = $pasted !== '' ? $pasted : $ivrmgr_settings->get('tts_credentials', '');

	if ($action === 'clear') {
		$ivrmgr_settings->delete('tts_credentials');
		ivrmgr_message('Credentials removed.');
		header('Location: tts_settings.php');
		exit;
	}

	if ($action === 'test') {
		if ($creds === '') {
			ivrmgr_message('No credentials to test — paste a service-account JSON or save one first.', 'negative');
		} else {
			try {
				$email = ivr_tts::verify($creds, $voice, $lang);
				$test_result = array('ok' => true, 'detail' => 'Valid — authenticated as ' . $email);
			} catch (Exception $e) {
				$test_result = array('ok' => false, 'detail' => $e->getMessage());
			}
		}
		// fall through to render (also persist a freshly pasted, valid cred below)
	}

	// store a pasted credential (validate structure only; the Test button proves it works)
	if ($pasted !== '') {
		$sa = json_decode($pasted, true);
		if (!is_array($sa) || empty($sa['client_email']) || empty($sa['private_key'])) {
			ivrmgr_message('That does not look like a service-account JSON (needs client_email and private_key).', 'negative');
		} else {
			$ivrmgr_settings->set('tts_credentials', $pasted);
			if ($action === 'save') {
				ivrmgr_message('Saved.');
				header('Location: tts_settings.php');
				exit;
			}
		}
	} elseif ($action === 'save') {
		ivrmgr_message('Saved.');
		header('Location: tts_settings.php');
		exit;
	}
}

$configured = $ivrmgr_settings->has('tts_credentials');
$voice = $ivrmgr_settings->get('tts_voice', 'en-US-Standard-C');
$lang = $ivrmgr_settings->get('tts_language', 'en-US');

$document['title'] = 'IVR Manager — TTS';
require_once "resources/header.php";
ivrmgr_render_flash();

$token = new token;
$t = $token->create($_SERVER['PHP_SELF']);

echo "<div class='action_bar'><div class='heading'><b>Google TTS settings</b></div>";
echo "<div class='actions'>" . ivrmgr_button(array('type' => 'button', 'label' => 'Back', 'icon' => 'chevron-left', 'link' => 'schedules.php')) . "</div><div style='clear:both;'></div></div>\n";

if ($test_result !== null) {
	$color = $test_result['ok'] ? '#3c763d' : '#a94442';
	echo "<div style='padding:.5em .8em; margin:.4em 0; border-left:4px solid " . $color . ";'>"
		. ($test_result['ok'] ? 'TTS OK — ' : 'TTS error — ') . ivrmgr_esc($test_result['detail']) . "</div>\n";
}

echo "<form method='post' action='tts_settings.php'>\n";
echo "<table width='100%'>\n";
echo "<tr><td class='vncell'>Status</td><td class='vtable'>" . ($configured ? "<b>Configured</b> (credentials stored, write-only)" : "Not configured") . "</td></tr>\n";
echo "<tr><td class='vncell'>Language code</td><td class='vtable'><input class='formfld' name='language' value='" . ivrmgr_esc($lang) . "' placeholder='en-US'></td></tr>\n";
echo "<tr><td class='vncell'>Voice name</td><td class='vtable'><input class='formfld' name='voice' value='" . ivrmgr_esc($voice) . "' placeholder='en-US-Standard-C'></td></tr>\n";
echo "<tr><td class='vncell'>Service-account JSON</td><td class='vtable'>"
	. "<textarea class='formfld' name='credentials' rows='6' placeholder='" . ($configured ? 'Leave blank to keep the current credentials, or paste a new JSON to replace them.' : 'Paste the Google service-account JSON (with the Cloud Text-to-Speech role).') . "'></textarea>"
	. "<div class='description'>Write-only — the stored key is never shown. Use <b>Test</b> to verify it works.</div></td></tr>\n";
echo "</table>\n";

echo "<input type='hidden' name='" . $t['name'] . "' value='" . $t['hash'] . "'>\n";
echo "<button type='submit' name='action' value='save' class='btn'>Save</button> ";
echo "<button type='submit' name='action' value='test' class='btn'>Test</button> ";
if ($configured) {
	echo "<button type='submit' name='action' value='clear' class='btn' onclick=\"return confirm('Remove the stored Google TTS credentials?');\">Remove credentials</button>";
}
echo "</form>\n";

require_once "resources/footer.php";
