<?php
/**
 * IVR Manager — business configuration: business name, prompt templates
 * ({placeholders} for greeting text), closure message lines, and default
 * destinations (on-hours / off-hours / emergency).
 */
require_once "root.php";
require_once "resources/require.php";
require_once "resources/check_auth.php";
require_once __DIR__ . "/resources/functions.php";
require_once __DIR__ . "/resources/classes/ivr_schedule.php";
require_once __DIR__ . "/resources/classes/ivr_settings.php";
require_once __DIR__ . "/resources/classes/ivr_destinations.php";
require_once __DIR__ . "/resources/classes/ivr_business.php";

if (!permission_exists('ivr_manager_business_manage')) {
	echo "access denied";
	exit;
}

$language = new text;
$text = $language->get();
$pdo = ivrmgr_pdo();
$domain_uuid = $_SESSION['domain_uuid'];
$domain_name = $_SESSION['domain_name'];

$ivrmgr_settings = new ivr_settings($pdo, $domain_uuid);
$dest = new ivr_destinations($pdo, $domain_uuid);
$business = new ivr_business($ivrmgr_settings, $domain_name);

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
	$token = new token;
	if (!$token->validate($_SERVER['PHP_SELF'])) {
		ivrmgr_message('Invalid token', 'negative');
		header('Location: business_settings.php');
		exit;
	}
	// business name + templating
	$ivrmgr_settings->set('business_name', trim($_POST['business_name']));
	$ivrmgr_settings->set('closure_opening', trim($_POST['closure_opening']));
	$ivrmgr_settings->set('closure_closing', trim($_POST['closure_closing']));
	$tmpls = array();
	if (isset($_POST['tmpl_name']) && is_array($_POST['tmpl_name'])) {
		foreach ($_POST['tmpl_name'] as $i => $tn) {
			$tn = trim($tn);
			if ($tn !== '') {
				$tmpls[$tn] = isset($_POST['tmpl_value'][$i]) ? trim($_POST['tmpl_value'][$i]) : '';
			}
		}
	}
	$ivrmgr_settings->set('templates', json_encode($tmpls));
	// default destinations
	$ivrmgr_settings->set('dest_on_hours', ivr_destinations::resolve($_POST, 'dest_on_hours'));
	$ivrmgr_settings->set('dest_off_hours', ivr_destinations::resolve($_POST, 'dest_off_hours'));
	$ivrmgr_settings->set('dest_emergency', ivr_destinations::resolve($_POST, 'dest_emergency'));
	ivrmgr_message('Saved.');
	header('Location: business_settings.php');
	exit;
}

$business_name = $ivrmgr_settings->get('business_name', '');
$closure_opening = $business->closure_opening();
$closure_closing = $business->closure_closing();
$templates = $business->templates();
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

echo "<form method='post' action='business_settings.php'>\n";

// ---- business name ----
echo "<h2>Business name</h2>\n";
echo "<input class='formfld' name='business_name' value='" . ivrmgr_esc($business_name) . "' placeholder='" . ivrmgr_esc($domain_name) . "'>";
echo "<div class='description'>Used in greeting text as <code>{business_name}</code> (falls back to the domain).</div>\n";

// ---- templates ----
echo "<h2 style='margin-top:1.25rem;'>Prompt templates</h2>\n";
echo "<div class='description'>Reusable named snippets — reference one in a greeting (or another template) as <code>{name}</code>. Nested references are resolved.</div>\n";
echo "<table class='list' id='tmpl_rows'><tr class='list-header'><th>Name</th><th>Value</th><th></th></tr>\n";
$rows = $templates;
if (empty($rows)) { $rows = array('' => ''); }
foreach ($rows as $tn => $tv) {
	echo tmpl_row_html($tn, $tv);
}
echo "</table>\n";
echo "<input type='button' class='btn' value='+ Add template' onclick='ivrmgrAddTmpl();'>\n";

// ---- closure message ----
echo "<h2 style='margin-top:1.25rem;'>Closure message</h2>\n";
echo "<div class='description'>Fixed opening/closing lines for generated closure greetings; may use <code>{business_name}</code> and templates.</div>\n";
echo "<label>Opening line</label><textarea class='formfld' name='closure_opening' rows='2' placeholder='Thank you for calling {business_name}.'>" . ivrmgr_esc($closure_opening) . "</textarea>\n";
echo "<label>Closing line</label><textarea class='formfld' name='closure_closing' rows='2' placeholder='We apologize for any inconvenience. Goodbye.'>" . ivrmgr_esc($closure_closing) . "</textarea>\n";

// ---- default destinations ----
echo "<h2 style='margin-top:1.25rem;'>Default destinations</h2>\n";
echo "<div class='description'>Reusable when building schedules — each may be a time condition, ring group, IVR, extension or voicemail.</div>\n";
echo "<table width='100%'>\n";
echo "<tr><td class='vncell'>On-hours (normal daytime)</td><td class='vtable'>" . $dest->render_select('dest_on_hours', $on) . "</td></tr>\n";
echo "<tr><td class='vncell'>Off-hours</td><td class='vtable'>" . $dest->render_select('dest_off_hours', $off) . "</td></tr>\n";
echo "<tr><td class='vncell'>Emergency</td><td class='vtable'>" . $dest->render_select('dest_emergency', $emerg) . "</td></tr>\n";
echo "</table>\n";

echo "<input type='hidden' name='" . $t['name'] . "' value='" . $t['hash'] . "'>\n";
echo "<button type='submit' class='btn' style='margin-top:1rem;'>Save</button>\n";
echo "</form>\n";

echo "<template id='tmpl_row_tpl'>" . tmpl_row_html('', '') . "</template>\n";
?>
<script>
function ivrmgrAddTmpl(){
	var tpl = document.getElementById('tmpl_row_tpl').innerHTML;
	var wrap = document.createElement('tbody'); wrap.innerHTML = tpl.trim();
	document.getElementById('tmpl_rows').appendChild(wrap.firstChild);
}
function ivrmgrRmTmpl(btn){ var tr = btn.closest('tr'); if (tr) tr.parentNode.removeChild(tr); }
</script>
<?php
require_once "resources/footer.php";

function tmpl_row_html($name, $value) {
	return "<tr class='list-row'>"
		. "<td><input class='formfld' name='tmpl_name[]' value='" . ivrmgr_esc($name) . "' placeholder='greeting'></td>"
		. "<td><input class='formfld' name='tmpl_value[]' value='" . ivrmgr_esc($value) . "' placeholder='Thank you for calling {business_name}.'></td>"
		. "<td><input type='button' class='btn' value='Remove' onclick='ivrmgrRmTmpl(this);'></td>"
		. "</tr>";
}
