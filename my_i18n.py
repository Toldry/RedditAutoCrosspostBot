"""Translates strings according to the target subreddit"""

# I've intentionally decided not to use `gettext` or `i18n` because their implementations appear to be overly complex for my simple use case

DEFAULT_LANGUAGE = 'en'

# I used google translate for these as placeholders, gonna need native speakers to do proper translations here
translations = {
    'POST_SUFFIX_TEXT':{
        'en':'''

---
^^🤖 ^^this ^^comment ^^was ^^written ^^by ^^a ^^bot. ^^beep ^^boop ^^🤖

^^feel ^^welcome ^^to ^^respond ^^'Bad ^^bot'/'Good ^^bot', ^^it's ^^useful ^^feedback. [^^github](https://github.com/Toldry/RedditAutoCrosspostBot)'''
,
    'es':'''

--- 
^^🤖 ^^este ^^comentario ^^fue ^^escrito ^^por ^^un ^^bot. ^^bip ^^boop ^^🤖

^^siéntase ^^bienvenido ^^a ^^responder ^^'Mal ^^bot' ^^/ ^^'Buen ^^bot', ^^es ^^una ^^retroalimentación ^^útil'''
,
    'de':'''

---
^^🤖 ^^Dieser ^^Kommentar ^^wurde ^^von ^^einem ^^Bot ^^geschrieben. ^^Beep ^^Boop ^^🤖

^^Gerne ^^kannst ^^du ^^mit ^^"Schlechter ^^Bot" ^^/ ^^"Guter ^^Bot" ^^antworten. ^^Es ^^ist ^^ein ^^nützliches ^^Feedback.'''
,
    'fr':'''

---
^^🤖 ^^ce ^^commentaire ^^a ^^été ^^écrit ^^par ^^un ^^bot. ^^bip ^^boop ^^🤖

^^N'hésitez ^^pas ^^à ^^répondre ^^'Mauvais ^^bot' ^^/ ^^'Bon ^^bot', ^^c'est ^^un ^^retour ^^utile'''
,
    'he':'''
---
^^🤖 ^^תגובה ^^זו ^^נכתבה ^^על ^^ידי ^^בוט. ^^ביפ ^^בופ ^^🤖

^^תרגישו ^^חופשי ^^להגיב ^^'בוט ^^רע' ^^/ ^^'בוט ^^טוב', ^^זה ^^משוב ^^שימושי'''
    },
    'RESPOND_TO_NEGATIVE_SENTIMENT':{
        'en':'''
Thanks for the feedback, would you mind detailing why this post/comment was inappropriate?

The creator of this bot will look at the responses and try to change the code to reduce incidences like these.'''
,
    'es':None,
    'de':None,
    'fr':None,
    'he':None,
    },
    'REPLY_TO_CROSSPOST':{
        'en':'''
I crossposted this from {source_subreddit_name_prefixed} to r/{target_subreddit} after seeing [this decently upvoted **human-made**^^1 comment]({source_comment_permalink}) (score={source_comment_score}), that seems to suggest that this post would be a good fit here too.
    
I checked on [repostsleuth.com](https://repostsleuth.com/search?postId={source_submission_id})^^2 before crossposting, to make sure this wasn't already posted before in r/{target_subreddit}.

I also waited {timedelta_string}^^3 before crossposting, in case a human might've wanted to crosspost this themselves.

If you think this was a mistake, go ahead and downvote; I'll remove posts with negative scores.

---
^^1 ^^- ^^Assuming ^^/u/{source_comment_author_name} ^^is ^^human

^^2 ^^- ^^Might ^^not ^^work ^^for ^^some ^^types ^^of ^^submissions, ^^such ^^as ^^videos

^^3 ^^- ^^This ^^value ^^was ^^chosen ^^arbitrarily

'''
,
    'es':'''
Publiqué esto de {source_subreddit_name_prefixed} a r/{target_subreddit} después de ver [este comentario **hecho por humanos** con una votación decente] ({source_comment_permalink}) (score={source_comment_score}), que parece Sugiero que esta publicación también encajaría bien aquí.
  
Si cree que esto fue un error, siga adelante y vote en contra; Eliminaré las publicaciones con puntuaciones negativas.
''',
    'de':'''
Ich habe diesen Pfosten von {source_subreddit_name_prefixed} nach r/{target_subreddit} gekreuzt, nachdem ich [diesen anständig hoochgewählten und von Menschen gemachten Kommentar] ({source_comment_permalink}) (score={source_comment_score}) gesehen habe und mir dachte, dass dieser Beitrag auch hier gut passen würde.

Wenn du der Meinung bist, dass es hier nicht passt, downvote den Post. Ich werde Beiträge mit negativen Ergebnissen entfernen.
'''
,
    'fr':'''
J'ai croisé ceci de {source_subreddit_name_prefixed} à r/{target_subreddit} après avoir vu [ce commentaire  fait par l'homme  décemment voté] ({source_comment_permalink}) (score={source_comment_score}), cela semble suggèrent que cet article conviendrait également ici.
  
Si vous pensez que c'était une erreur, allez-y et votez contre; Je supprimerai les messages avec des scores négatifs.
'''
    ,
    'he':'''
עשיתי קרוספוסט מ- {source_subreddit_name_prefixed} ל- r/{target_subreddit} אחרי שראיתי את [התגובה הזו **שנכתבה על ידי בן-אדם** עם ניקוד נאות] ({source_comment_permalink}) (ניקוד = {source_comment_score}), שמרמזת כי פוסט זה מתאים גם לכאן.
  
אם אתה חושב שזו טעות, הרגישו חופשי להצביע-מטה; אסיר פוסטים עם ניקד שלילי.
    ''',
    },
    'THE_USER_WHO_COMMENTED':{
    'en':'''the user who commented''',
    'es':None,
    'de':'''der Nutzer, der geantwortet hat''',
    'fr':None,
    'he':None,
    },
    'THATS_WHERE_WE_ARE':{
    'en':'''Yes, that's where we are.''',
    'es':None,
    'de':'''Ja, hier sind wir''',
    'fr':None,
    'he':None,
    },
    'NONEXISTENT_SUBREDDIT':{
    'en':'''The subreddit r/{target_subreddit} does not exist. ''',
    'es':None,
    'de':'''Das Unter r/{target_subreddit} existiert nicht''',
    'fr':None,
    'he':None,
    },
    'PROMPT_NONEXISTENT_SUBREDDIT_CREATION':{
    'en':'''Consider [creating it](/subreddits/create?name={target_subreddit}).''',
    'es':None,
    'de':'''Vielleicht [sollte es erstellen](/subreddits/create?name={target_subreddit}).''',
    'fr':None,
    'he':None,
    },
    'FOUND_POST_WITH_SAME_CONTENT':{
    'en':'''I found [this post]({same_content_post_url}) in r/{target_subreddit} with the same link as this post.''',
    'es':None,
    'de':'''Ich habe [diesen Post]({same_content_post_url}) in r/{target_subreddit} gefunden, der den selben Link enthält.''',
    'fr':None,
    'he':None,
    },
}

# TODO: use language detection algorithms to automate this
subreddit_language_map = {
    # 'ani_bm'            :'he',
    # 'israel'            :'he',
    # 'yo_elvr'           :'es',
    # 'ich_iel'           :'de',
    # 'LTB_iel'           :'de',
    # 'schkreckl'         :'de',
    # 'wasletztepreis'    :'de',
    # 'OkBrudiMongo'      :'de',
    # 'GeschichtsMaimais' :'de',
    # 'moi_dlvv'          :'fr',
    # 'rance'             :'fr',
}

def get_translated_string(string_key, target_subreddit, add_suffix=True):
    if target_subreddit in subreddit_language_map:
        target_language = subreddit_language_map[target_subreddit]
        if target_language in translations[string_key] and translations[string_key][target_language] is not None:
            ret = translations[string_key][target_language]
            ret += translations['POST_SUFFIX_TEXT'][target_language]
            return ret
    
    ret = translations[string_key][DEFAULT_LANGUAGE]
    
    if add_suffix:
        ret += translations['POST_SUFFIX_TEXT'][DEFAULT_LANGUAGE]

    return ret

    
        
