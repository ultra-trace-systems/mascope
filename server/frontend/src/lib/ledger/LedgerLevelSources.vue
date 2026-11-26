<script setup>
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'

import { BaseLevelTag } from '@/lib/base'
import { num } from '@/lib/formatters'
import {
  bestStatedLevel,
  commonLine,
  levelMeta,
  levelRank,
  levelSplit,
  membersAtLevel,
  unstatedLines
} from '@/lib/levels'

/**
 * Where a batch row's level comes from: the members whose run states it, the
 * evidence and the lift they wrote, and how to open them. Shown from the batch
 * ledger's Level cell; kept apart from the ledger, so a card for a batch row
 * (MAS-1298) can show the same.
 *
 * The batch states no level of its own. The ledger shows the level most of a
 * batch peak's members' runs stated for the consensus formula, so "the sample
 * the level comes from" is a set, and the set can hide better ones. This lists
 * the split, the shown level's lines, the best level any member states where
 * it is better, and the members that state none. A live batch-level claim,
 * where the row shows one, comes first.
 *
 * It reads nothing itself: the members are the level-sources read
 * (`GET /batch-peaks/batch/{id}/levels/{batch_peak_id}`), asked by the
 * ledger on the click, and opening a member is the ledger's.
 */
const props = defineProps({
  /** The live batch-level claim's level the row shows instead, or null:
   *  `level`, `evidence` and `lift`. */
  claim: { type: Object, default: null },
  /** The members' level the ledger shows (`member_level`). */
  memberLevel: { type: String, default: null },
  /** The ledger's split (`member_levels`), until the read answers. */
  memberLevels: { type: Object, default: null },
  /** The level-sources read's body, or null while it is asked. */
  sources: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  /** Why the read failed, or null. */
  error: { type: String, default: null },
  /** An earlier run's ledger, whose snapshot keeps no member levels. */
  snapshot: { type: Boolean, default: false },
  /** Whether the reader may run Assign batch, and why it is held back if so. */
  canAssign: { type: Boolean, default: false },
  assignBlocked: { type: String, default: null }
})

const emit = defineEmits(['open-member', 'assign'])

const members = computed(() => props.sources?.data ?? [])
const shownMembers = computed(() => props.sources?.member_level ?? props.memberLevel)
const split = computed(() =>
  levelSplit(props.sources?.member_levels ?? props.memberLevels, shownMembers.value)
)
const total = computed(() => split.value.reduce((sum, entry) => sum + entry.count, 0))
const samples = (n) => `${n} ${n === 1 ? 'sample' : 'samples'}`

// The level the row shows: the claim's, else the members'.
const shown = computed(() => props.claim?.level ?? shownMembers.value)
const atShown = computed(() => membersAtLevel(members.value, shownMembers.value))
const lines = computed(() =>
  ['level_evidence', 'level_lift'].map((key) => ({
    key,
    label: key === 'level_evidence' ? 'Why' : 'Would lift it',
    line: commonLine(atShown.value, key)
  }))
)

// The best level any member states, where it is better than the one shown:
// the nearest answer to what it would take to raise the row.
const better = computed(() => {
  const best = bestStatedLevel(props.sources?.member_levels ?? props.memberLevels)
  if (!best || !shown.value || levelRank(best) >= levelRank(shown.value)) return null
  return { level: best, count: split.value.find((entry) => entry.level === best)?.count ?? 0 }
})

const unstated = computed(() => unstatedLines(props.sources?.unstated))

// The level whose members are listed, to pick one other than the brightest.
const listing = ref(null)
watch(
  () => props.sources,
  () => (listing.value = null)
)
const listed = computed(() => (listing.value ? membersAtLevel(members.value, listing.value) : []))

function openLevel(level) {
  const at = membersAtLevel(members.value, level)
  if (at.length) emit('open-member', at[0], at, level)
}
function openOne(member) {
  emit('open-member', member, membersAtLevel(members.value, member.level), member.level)
}

const when = (iso) => {
  const date = iso ? new Date(iso) : null
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : '-'
}
const intensity = (value) => (value == null ? '-' : num.peakIntensity.format(value))
</script>

<template>
  <div class="level-sources" data-testid="level-sources-popover">
    <!-- A live claim is what the row shows: it comes first. -->
    <section v-if="claim" class="claim" data-testid="level-claim">
      <div class="heading">
        <BaseLevelTag :level="claim.level" :evidence="claim.evidence" :lift="claim.lift" />
        <span class="title">A batch-level verdict states this level</span>
      </div>
      <p v-if="claim.evidence" class="line"><b>Why:</b> {{ claim.evidence }}</p>
      <p v-if="claim.lift" class="line"><b>Would lift it:</b> {{ claim.lift }}</p>
      <p class="note">Under it, what the members' own runs stated.</p>
    </section>

    <p v-if="snapshot" class="note" data-testid="level-snapshot">
      An earlier run's ledger keeps no member levels. Open the current run to see which samples
      state them.
    </p>

    <template v-else>
      <div class="heading">
        <BaseLevelTag v-if="shownMembers" :level="shownMembers" />
        <span class="title">
          {{
            levelMeta(shownMembers)
              ? `The level most members' runs state: ${levelMeta(shownMembers).name}`
              : "The members' runs state no level"
          }}
        </span>
      </div>

      <p v-if="error" class="warning" data-testid="level-sources-error">{{ error }}</p>
      <p v-else-if="loading && !sources" class="note">Reading the members' levels...</p>

      <!-- The shown members' level: its lines as their runs wrote them. -->
      <template v-if="sources && atShown.length">
        <template v-for="{ key, label, line } in lines" :key="key">
          <p v-if="line" class="line" :data-testid="`level-${key}`">
            <b>{{ label }}:</b> {{ line.text }}
            <span v-if="line.count < line.of" class="note">
              ({{ line.count }} of {{ line.of }} samples at {{ shownMembers }} say this)
            </span>
          </p>
        </template>
      </template>

      <!-- The split, in the scale's order, the members' level marked. -->
      <table v-if="split.length" class="split" data-testid="level-split">
        <tbody>
          <tr
            v-for="entry in split"
            :key="entry.level"
            :class="{ shown: entry.shown }"
            data-testid="level-split-row"
            :data-level="entry.level"
          >
            <td><BaseLevelTag :level="entry.level" /></td>
            <td class="count">{{ samples(entry.count) }}</td>
            <td class="mark">
              <span v-if="entry.shown" data-testid="level-shown-mark">{{
                claim ? "members' level" : 'shown'
              }}</span>
            </td>
            <td class="actions">
              <Button
                label="Open a sample"
                text
                size="small"
                :disabled="!sources"
                data-testid="level-open"
                :aria-label="`Open the brightest sample whose run states ${entry.level}`"
                @click="openLevel(entry.level)"
              />
              <Button
                :label="listing === entry.level ? 'Hide' : `All ${entry.count}`"
                text
                size="small"
                :disabled="!sources"
                data-testid="level-list"
                :aria-label="`List the samples whose run states ${entry.level}`"
                @click="listing = listing === entry.level ? null : entry.level"
              />
            </td>
          </tr>
        </tbody>
      </table>

      <!-- Every member at one level, to pick another than the brightest. -->
      <table v-if="listed.length" class="members" data-testid="level-members">
        <thead>
          <tr>
            <th>Sample</th>
            <th>Acquired</th>
            <th class="num">Intensity</th>
            <th>Level</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="member in listed" :key="member.sample_item_id" data-testid="level-member">
            <td>
              <button type="button" class="link" @click="openOne(member)">
                {{ member.sample_item_name }}
              </button>
            </td>
            <td>{{ when(member.datetime_utc) }}</td>
            <td class="num">{{ intensity(member.intensity) }}</td>
            <td>{{ member.level }}</td>
          </tr>
        </tbody>
      </table>

      <!-- A better level some members state: what would raise the row. -->
      <div v-if="better" class="better" data-testid="level-better">
        <span>
          {{ samples(better.count) }} {{ better.count === 1 ? 'reads' : 'read' }}
          <b>{{ better.level }}</b>: the nearest answer to what would raise this row.
        </span>
        <Button
          label="Open a sample"
          text
          size="small"
          :disabled="!sources"
          data-testid="level-better-open"
          @click="openLevel(better.level)"
        />
      </div>

      <!-- The members that state none, and what would give them one. -->
      <div v-if="unstated.length" class="unstated" data-testid="level-unstated">
        <p v-for="line in unstated" :key="line" class="line">{{ line }}.</p>
        <span v-if="canAssign" v-tooltip.top="assignBlocked">
          <Button
            label="Assign batch"
            size="small"
            outlined
            :disabled="Boolean(assignBlocked)"
            data-testid="level-assign"
            @click="emit('assign')"
          />
        </span>
      </div>

      <p v-if="sources && total" class="note">
        The batch states no level of its own: this is what its members' runs stated for the
        consensus formula, a tie reading as the weaker level.
      </p>
    </template>
  </div>
</template>

<style scoped>
.level-sources {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  width: 30rem;
  max-width: 90vw;
  font-size: 0.82rem;
}
.heading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.title {
  font-weight: 600;
}
.claim {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding-bottom: 0.45rem;
  border-bottom: 1px solid var(--p-content-border-color);
}
.line,
.note,
.warning {
  margin: 0;
}
.note {
  opacity: 0.75;
}
.warning {
  padding: 0.25rem 0.4rem;
  border: 1px solid var(--state-warning);
  border-radius: 4px;
}
.split,
.members {
  border-collapse: collapse;
  width: 100%;
}
.split td,
.members td,
.members th {
  padding: 0.1rem 0.35rem;
  text-align: left;
}
.members th {
  font-weight: 600;
  font-size: 0.75rem;
}
.split tr.shown {
  background: var(--p-content-hover-background);
}
.count,
.num {
  font-variant-numeric: tabular-nums;
}
.num {
  text-align: right;
}
.mark {
  font-size: 0.72rem;
  color: var(--p-text-muted-color);
}
.actions {
  text-align: right;
  white-space: nowrap;
}
.members {
  max-height: 14rem;
  overflow-y: auto;
  display: block;
}
.link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--p-primary-color);
  font: inherit;
  cursor: pointer;
  text-align: left;
}
.link:hover {
  text-decoration: underline;
}
.better,
.unstated {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.25rem;
}
.better {
  padding: 0.3rem 0.45rem;
  border: 1px solid var(--p-primary-color);
  border-radius: 4px;
}
</style>
