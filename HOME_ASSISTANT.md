# Dapple with Home Assistant

Dapple has no Home Assistant integration to install. It exposes plain HTTP endpoints, and
Home Assistant's built-in `rest_command` calls them. Everything below goes in your
`configuration.yaml`.

Replace `dapple.lan:8080` throughout with wherever Dapple is running — an address like
`192.168.1.10:8080` is fine.

---

## The one thing to know first

**Every call names a group.** Dapple has no "all lights" command, because a group is the thing
that holds a pattern and there is no such thing as a pattern for everything at once.

Group ids are what you write in automations. Find them on the Strands tab — the group named
"Christmas tree" gets the id `christmas-tree` — or by opening
`http://dapple.lan:8080/api/groups` in a browser.

**A group's id never changes, even when you rename it.** Rename "Tree" to "Big tree" and your
automations keep working.

---

## Commands

```yaml
rest_command:
  dapple_preset:
    url: "http://dapple.lan:8080/api/groups/{{ group }}/preset"
    method: POST
    content_type: "application/json"
    payload: '{"name": "{{ name }}"}'

  dapple_brightness:
    url: "http://dapple.lan:8080/api/groups/{{ group }}/brightness"
    method: POST
    content_type: "application/json"
    payload: '{"value": {{ value }}}'

  dapple_on:
    url: "http://dapple.lan:8080/api/groups/{{ group }}/on"
    method: POST

  dapple_off:
    url: "http://dapple.lan:8080/api/groups/{{ group }}/off"
    method: POST
```

Call them like this:

```yaml
- service: rest_command.dapple_preset
  data:
    group: tree
    name: Halloween
```

The preset name goes in the body rather than the URL so that names with spaces — `Warm white`
is one of the built-in presets — need no escaping.

---

## A dropdown per group

This gives you a picker on a dashboard that sets the tree, with "Off" as one of the choices:

```yaml
input_select:
  dapple_tree:
    name: Tree
    options: ["Off", "Halloween", "Christmas", "Warm white"]

automation:
  - alias: Tree palette
    trigger:
      - platform: state
        entity_id: input_select.dapple_tree
    action:
      - choose:
          - conditions: "{{ trigger.to_state.state == 'Off' }}"
            sequence:
              - service: rest_command.dapple_off
                data:
                  group: tree
        default:
          - service: rest_command.dapple_preset
            data:
              group: tree
              name: "{{ trigger.to_state.state }}"
```

For a second group, copy both blocks and change `dapple_tree` → `dapple_porch` and
`group: tree` → `group: porch`.

The `options:` list has to be kept in step with your presets by hand. This sensor lets you spot
when they've drifted apart:

```yaml
rest:
  - resource: "http://dapple.lan:8080/api/presets"
    scan_interval: 300
    sensor:
      - name: dapple_presets
        value_template: "{{ value_json | list | join(',') }}"
```

---

## Turning everything off

Since there's no whole-house command, list your groups explicitly:

```yaml
script:
  dapple_all_off:
    alias: All Twinkly off
    sequence:
      - repeat:
          for_each: ["tree", "porch"]
          sequence:
            - service: rest_command.dapple_off
              data:
                group: "{{ repeat.item }}"
```

You have to add a new group to that list by hand. That's deliberate: building the list from a
sensor would mean the script quietly does nothing at all on the night Dapple happens to be
unreachable, and you'd find the lights still on in the morning. A list you maintain fails in a
way you'll notice — the forgotten group stays lit — rather than silently.

---

## The UI on a dashboard

```yaml
panel_iframe:
  dapple:
    title: Twinkly
    icon: mdi:string-lights
    url: "http://dapple.lan:8080/"
```

The page remembers where you are in its address, so you can link straight to a particular
screen:

| Address | Opens on |
|---|---|
| `…:8080/` | the pattern editor |
| `…:8080/#pattern/tree` | the pattern editor for the `tree` group |
| `…:8080/#strands` | the strand and group list |

---

## Voice

Once the `input_select` above exists, "Hey Google, set Tree to Halloween" works through Home
Assistant's usual assistant exposure — no extra Dapple configuration.

For a spoken command that doesn't map to a dropdown, call the `rest_command` from an intent
script:

```yaml
intent_script:
  SetTwinkly:
    speech:
      text: "Setting the tree to {{ preset }}"
    action:
      - service: rest_command.dapple_preset
        data:
          group: tree
          name: "{{ preset }}"
```

---

## Checking it worked

`rest_command` reports success whenever Dapple answers, which it does even when a strand is
unplugged — the reply says which strands took the pattern and which didn't. To act on that, use
the response:

```yaml
- service: rest_command.dapple_preset
  data:
    group: tree
    name: Halloween
  response_variable: result
- condition: template
  value_template: "{{ not result.content.ok }}"
- service: notify.persistent_notification
  data:
    message: >-
      Twinkly trouble:
      {{ result.content.results | rejectattr('ok') | map(attribute='name') | join(', ') }}
```

`http://dapple.lan:8080/api/health` is a quick check that Dapple is up and can see every strand,
if you'd rather monitor it that way.

---

## Everything else

The full list of endpoints, including creating and rearranging groups, is in
[DEVELOPING.md](DEVELOPING.md).
