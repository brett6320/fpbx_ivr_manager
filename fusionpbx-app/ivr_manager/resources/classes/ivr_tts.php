<?php
/**
 * Google Cloud Text-to-Speech for the FusionPBX PHP app.
 *
 * Service-account auth without any Composer/library: an RS256 JWT is signed with
 * openssl_sign (openssl is in the FusionPBX PHP build), exchanged for a
 * short-lived access token, and sent as a Bearer token. Requests LINEAR16 @ 8 kHz
 * mono — a WAV container FreeSWITCH plays directly. Matches the Python app.
 */
class ivr_tts {

	const REC_TAG = '[fpbx-ivr-manager:managed]';
	const GENERATED_PREFIX = 'ivrmgr_';

	private $pdo;
	private $domain_uuid;
	private $domain_name;
	private $creds_json;
	private $voice;
	private $language;
	private $recordings_dir;

	public function __construct($pdo, $domain_uuid, $domain_name, $creds_json, $voice, $language, $recordings_dir) {
		$this->pdo = $pdo;
		$this->domain_uuid = $domain_uuid;
		$this->domain_name = $domain_name;
		$this->creds_json = $creds_json;
		$this->voice = $voice !== '' ? $voice : 'en-US-Standard-C';
		$this->language = $language !== '' ? $language : 'en-US';
		$this->recordings_dir = rtrim((string) $recordings_dir, '/');
	}

	private static function b64url($data) {
		return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
	}

	private static function http_post($url, $body, $headers) {
		if (!function_exists('curl_init')) {
			throw new Exception('php-curl is required for Google TTS');
		}
		$ch = curl_init($url);
		curl_setopt_array($ch, array(
			CURLOPT_POST => true,
			CURLOPT_POSTFIELDS => $body,
			CURLOPT_HTTPHEADER => $headers,
			CURLOPT_RETURNTRANSFER => true,
			CURLOPT_TIMEOUT => 30,
		));
		$resp = curl_exec($ch);
		$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
		$err = curl_error($ch);
		curl_close($ch);
		if ($resp === false) {
			throw new Exception('network error: ' . $err);
		}
		return array($code, $resp);
	}

	private function service_account() {
		$sa = json_decode((string) $this->creds_json, true);
		if (!is_array($sa) || empty($sa['client_email']) || empty($sa['private_key'])) {
			throw new Exception('invalid service-account JSON (needs client_email and private_key)');
		}
		return $sa;
	}

	private function access_token() {
		$sa = $this->service_account();
		$now = time();
		$header = self::b64url(json_encode(array('alg' => 'RS256', 'typ' => 'JWT')));
		$claims = self::b64url(json_encode(array(
			'iss' => $sa['client_email'],
			'scope' => 'https://www.googleapis.com/auth/cloud-platform',
			'aud' => 'https://oauth2.googleapis.com/token',
			'iat' => $now,
			'exp' => $now + 3600,
		)));
		$input = $header . '.' . $claims;
		$sig = '';
		if (!openssl_sign($input, $sig, $sa['private_key'], OPENSSL_ALGO_SHA256)) {
			throw new Exception('JWT signing failed — check the private_key');
		}
		$jwt = $input . '.' . self::b64url($sig);
		list($code, $resp) = self::http_post(
			'https://oauth2.googleapis.com/token',
			http_build_query(array(
				'grant_type' => 'urn:ietf:params:oauth:grant-type:jwt-bearer',
				'assertion' => $jwt,
			)),
			array('Content-Type: application/x-www-form-urlencoded'));
		$data = json_decode($resp, true);
		if ($code !== 200 || empty($data['access_token'])) {
			throw new Exception('token request rejected (' . $code . ')');
		}
		return $data['access_token'];
	}

	/** Synthesize text to WAV bytes (LINEAR16 @ 8 kHz). */
	public function synthesize($text) {
		$token = $this->access_token();
		$body = json_encode(array(
			'input' => array('text' => $text),
			'voice' => array('languageCode' => $this->language, 'name' => $this->voice),
			'audioConfig' => array('audioEncoding' => 'LINEAR16', 'sampleRateHertz' => 8000),
		));
		list($code, $resp) = self::http_post(
			'https://texttospeech.googleapis.com/v1/text:synthesize',
			$body,
			array('Authorization: Bearer ' . $token, 'Content-Type: application/json; charset=utf-8'));
		$data = json_decode($resp, true);
		if ($code !== 200 || empty($data['audioContent'])) {
			$msg = isset($data['error']['message']) ? $data['error']['message'] : ('http ' . $code);
			throw new Exception('synthesis failed: ' . $msg);
		}
		return base64_decode($data['audioContent']);
	}

	/** Verify creds are valid without revealing them. Returns the account email. */
	public static function verify($creds_json, $voice, $language) {
		$t = new self(null, null, null, $creds_json, $voice, $language, null);
		$t->synthesize('test');       // throws on any failure
		$sa = json_decode((string) $creds_json, true);
		return is_array($sa) && isset($sa['client_email']) ? $sa['client_email'] : '';
	}

	private static function slug($text) {
		$s = strtolower((string) $text);
		$s = preg_replace('/[^a-z0-9]+/', '_', $s);
		$s = trim($s, '_');
		return $s !== '' ? $s : 'schedule';
	}

	/** Store WAV as a managed recording; refuses to overwrite a foreign one. */
	private function store_recording($name, $wav, $description) {
		$filename = $name . '.wav';
		// ownership guard
		$stmt = $this->pdo->prepare(
			"select recording_uuid, recording_description from v_recordings where domain_uuid = :d and recording_name = :n");
		$stmt->execute(array(':d' => $this->domain_uuid, ':n' => $name));
		$existing = $stmt->fetch(PDO::FETCH_ASSOC);
		if ($existing && strpos((string) $existing['recording_description'], self::REC_TAG) === false) {
			throw new Exception('recording ' . $name . ' exists and was not created by this app');
		}
		// write the file to the domain recordings dir
		if ($this->recordings_dir === '') {
			throw new Exception('recordings directory is not configured');
		}
		if (!is_dir($this->recordings_dir) && !@mkdir($this->recordings_dir, 0770, true) && !is_dir($this->recordings_dir)) {
			throw new Exception('cannot create recordings dir ' . $this->recordings_dir);
		}
		$path = $this->recordings_dir . '/' . $filename;
		if (@file_put_contents($path, $wav) === false) {
			throw new Exception('cannot write recording file ' . $path);
		}
		@chmod($path, 0640);
		$tagged = trim($description . ' ' . self::REC_TAG);
		$b64 = base64_encode($wav);
		if ($existing) {
			$stmt = $this->pdo->prepare(
				"update v_recordings set recording_filename = :f, recording_base64 = :b, recording_description = :desc where recording_uuid = :id");
			$stmt->execute(array(':f' => $filename, ':b' => $b64, ':desc' => $tagged, ':id' => $existing['recording_uuid']));
		} else {
			$stmt = $this->pdo->prepare(
				"insert into v_recordings (recording_uuid, domain_uuid, recording_name, recording_filename, recording_base64, recording_description) "
				. "values (:u, :d, :n, :f, :b, :desc)");
			$stmt->execute(array(
				':u' => ivr_schedule::uuid(), ':d' => $this->domain_uuid, ':n' => $name,
				':f' => $filename, ':b' => $b64, ':desc' => $tagged));
		}
		return $filename;
	}

	/** Synthesize a closure greeting and store it; returns the recording filename. */
	public function generate_greeting($extension, $label, $text) {
		$wav = $this->synthesize($text);
		$name = self::GENERATED_PREFIX . 'schedule_' . $extension . '_' . self::slug($label);
		return $this->store_recording($name, $wav, $label);
	}
}
